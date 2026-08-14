"""Integração CallOrchestrator + CallRepository — garante que os pontos de
gravação (turn/escalation/action/call) do orquestrador realmente persistem,
usando SQLite in-memory no lugar do Postgres de produção."""
import pytest
from sqlalchemy import select

from voxisp.connectors.mock import MockISPConnector
from voxisp.db.models import Action, Escalation, Turn
from voxisp.db.repository import CallRepository
from voxisp.db.session import build_session_maker, init_models
from voxisp.orchestrator.llm_client import StubLLMClient
from voxisp.orchestrator.turn_manager import CallOrchestrator


@pytest.fixture
async def repository():
    engine, session_maker = build_session_maker("sqlite+aiosqlite:///:memory:")
    await init_models(engine)
    try:
        yield CallRepository(session_maker), session_maker
    finally:
        await engine.dispose()


async def test_greet_creates_call_row_lazily(repository):
    repo, _session_maker = repository
    orch = CallOrchestrator(
        connector=MockISPConnector(), llm=StubLLMClient(), repository=repo, ani="+5511900000000", dnis="0800"
    )

    assert orch.call_id is None  # nada persistido antes do primeiro turno
    await orch.greet()

    assert orch.call_id is not None
    call = await repo.get_call(orch.call_id)
    assert call.ani == "+5511900000000"
    assert call.ended_at is None


async def test_turns_are_persisted_in_order(repository):
    repo, session_maker = repository
    orch = CallOrchestrator(connector=MockISPConnector(), llm=StubLLMClient(), repository=repo)

    await orch.greet()
    await orch.identify("111.444.777-35")

    async with session_maker() as session:
        rows = (
            (await session.execute(select(Turn).where(Turn.call_id == orch.call_id).order_by(Turn.seq)))
            .scalars()
            .all()
        )
    assert [r.role for r in rows] == ["assistant", "customer", "assistant"]
    assert [r.seq for r in rows] == [1, 2, 3]


async def test_escalation_persists_and_closes_the_call(repository):
    repo, session_maker = repository
    orch = CallOrchestrator(connector=MockISPConnector(), llm=StubLLMClient(), repository=repo)

    await orch.greet()
    await orch.identify("111.444.777-35")
    result = await orch.handle_utterance("quero falar com uma pessoa")

    assert result.escalate is True
    call = await repo.get_call(orch.call_id)
    assert call.outcome == "escalated"
    assert call.contained is False
    assert call.ended_at is not None
    assert call.protocol == result.protocol_number

    async with session_maker() as session:
        escalations = (
            (await session.execute(select(Escalation).where(Escalation.call_id == orch.call_id))).scalars().all()
        )
    assert len(escalations) == 1
    assert escalations[0].reason == "solicitação explícita de atendente"


async def test_trust_unlock_persists_an_action(repository):
    repo, session_maker = repository
    orch = CallOrchestrator(connector=MockISPConnector(), llm=StubLLMClient(), repository=repo)

    await orch.greet()
    await orch.identify("111.444.777-35")  # sub-001 tem fatura em aberto -> elegível
    await orch.handle_utterance("quero o desbloqueio de confiança")

    async with session_maker() as session:
        actions = (
            (await session.execute(select(Action).where(Action.call_id == orch.call_id))).scalars().all()
        )
    assert len(actions) == 1
    assert actions[0].tool_name == "request_trust_unlock"
    assert actions[0].status == "success"


async def test_finish_marks_call_contained(repository):
    repo, _session_maker = repository
    orch = CallOrchestrator(connector=MockISPConnector(), llm=StubLLMClient(), repository=repo)

    await orch.greet()
    await orch.identify("111.444.777-35")
    await orch.finish()

    call = await repo.get_call(orch.call_id)
    assert call.contained is True
    assert call.outcome == "contained"
    assert call.ended_at is not None


async def test_orchestrator_without_repository_is_a_no_op():
    """Comportamento padrão (sem `repository`) não deve tentar persistir nada."""
    orch = CallOrchestrator(connector=MockISPConnector(), llm=StubLLMClient())
    await orch.greet()
    await orch.finish()  # não deve levantar exceção mesmo sem repository/call_id
    assert orch.call_id is None
