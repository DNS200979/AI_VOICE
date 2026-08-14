"""Testes do CallRepository — SQLite in-memory (aiosqlite), sem exigir
Postgres rodando. O mesmo `CallRepository` funciona contra Postgres em
produção (`build_session_maker(settings.database_url)`), já que só usa
tipos/funções portáveis (`Uuid`, `JSON`, `func.now()`) nos modelos ORM.
"""
import pytest

from voxisp.db.models import Call
from voxisp.db.repository import CallRepository
from voxisp.db.session import build_session_maker, init_models


@pytest.fixture
async def repository():
    engine, session_maker = build_session_maker("sqlite+aiosqlite:///:memory:")
    await init_models(engine)
    try:
        yield CallRepository(session_maker)
    finally:
        await engine.dispose()


async def test_create_call_and_fetch(repository: CallRepository):
    call_id = await repository.create_call(ani="+5511999990001", dnis="0800123456")

    call = await repository.get_call(call_id)

    assert call.ani == "+5511999990001"
    assert call.dnis == "0800123456"
    assert call.contained is False
    assert call.ended_at is None


async def test_add_turn_persists_role_and_transcript(repository: CallRepository):
    call_id = await repository.create_call(ani="a", dnis="b")

    await repository.add_turn(call_id=call_id, seq=1, role="assistant", transcript="Olá!")
    await repository.add_turn(
        call_id=call_id, seq=2, role="customer", transcript="Minha internet caiu", intent="NET-01"
    )

    # Sem um método de listagem dedicado ainda — valida indiretamente via
    # sessão bruta, o que também serve de smoke test do relacionamento ORM.
    async with repository._session_maker() as session:
        result = await session.get(Call, call_id)
        await session.refresh(result, attribute_names=["turns"])
        assert len(result.turns) == 2
        assert {t.role for t in result.turns} == {"assistant", "customer"}
        assert any(t.intent == "NET-01" for t in result.turns)


async def test_add_action_enforces_idempotency_key_uniqueness(repository: CallRepository):
    call_id = await repository.create_call(ani="a", dnis="b")

    await repository.add_action(
        call_id=call_id,
        tool_name="reboot_cpe",
        params={"cpe_serial": "ONU-1"},
        result={"success": True},
        status="success",
        idempotency_key="idem-fixo",
    )

    with pytest.raises(Exception):  # noqa: B017 - IntegrityError do driver, não vale acoplar
        await repository.add_action(
            call_id=call_id,
            tool_name="reboot_cpe",
            params={"cpe_serial": "ONU-1"},
            result={"success": True},
            status="success",
            idempotency_key="idem-fixo",  # spec §5: idempotência evita reboot duplicado
        )


async def test_add_escalation_persists_context_payload(repository: CallRepository):
    call_id = await repository.create_call(ani="a", dnis="b")

    await repository.add_escalation(
        call_id=call_id,
        reason="solicitação explícita de atendente",
        queue="humano",
        context_payload={"intent": "ESC-04"},
    )

    async with repository._session_maker() as session:
        result = await session.get(Call, call_id)
        await session.refresh(result, attribute_names=["escalations"])
        assert len(result.escalations) == 1
        assert result.escalations[0].context_payload == {"intent": "ESC-04"}


async def test_finish_call_sets_ended_at_and_duration(repository: CallRepository):
    call_id = await repository.create_call(ani="a", dnis="b")

    await repository.finish_call(
        call_id=call_id, outcome="contained", contained=True, protocol="2026-0814-00001"
    )

    call = await repository.get_call(call_id)
    assert call.outcome == "contained"
    assert call.contained is True
    assert call.ended_at is not None
    assert call.protocol == "2026-0814-00001"
    assert call.duration_s is not None and call.duration_s >= 0


async def test_link_incident(repository: CallRepository):
    call_id = await repository.create_call(ani="a", dnis="b")

    await repository.link_incident(call_id=call_id, incident_id="inc-123", source="massivo")

    async with repository._session_maker() as session:
        result = await session.get(Call, call_id)
        await session.refresh(result, attribute_names=[])  # no-op, só garante objeto vivo
    # Sem relationship dedicada para incident_link no modelo Call — valida
    # via query direta.
    from sqlalchemy import select

    from voxisp.db.models import IncidentLink

    async with repository._session_maker() as session:
        rows = (await session.execute(select(IncidentLink).where(IncidentLink.call_id == call_id))).scalars().all()
        assert len(rows) == 1
        assert rows[0].incident_id == "inc-123"
