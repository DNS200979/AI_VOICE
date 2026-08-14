"""Testes ponta a ponta do orquestrador — reproduz o exemplo de fluxo
"estou sem internet" da spec §7.2, e a regra de transbordo em 1 palavra."""
from voxisp.connectors.mock import MockISPConnector
from voxisp.orchestrator.llm_client import StubLLMClient
from voxisp.orchestrator.turn_manager import CallOrchestrator


def _new_orchestrator() -> CallOrchestrator:
    return CallOrchestrator(connector=MockISPConnector(), llm=StubLLMClient())


async def test_identification_flow():
    orch = _new_orchestrator()
    await orch.greet()
    result = await orch.identify("111.444.777-35")
    assert "João" in result.text
    assert orch.subscriber is not None
    assert orch.subscriber.id == "sub-001"


async def test_invalid_cpf_does_not_identify():
    orch = _new_orchestrator()
    await orch.greet()
    result = await orch.identify("111")
    assert orch.subscriber is None
    assert "repetir" in result.text.lower()


async def test_net01_massive_flow_matches_spec_example():
    """Reproduz §7.2: cliente sem internet, PON com massivo, resposta com
    previsão e protocolo, sem abrir OS individual."""
    orch = _new_orchestrator()
    await orch.greet()
    await orch.identify("111.444.777-35")

    result = await orch.handle_utterance("Minha internet parou.")

    assert result.escalate is False
    assert result.protocol_number is not None
    assert "interrupção" in result.text.lower() or "previsão" in result.text.lower()
    assert "NET-03.massive_check" in orch._diagnostics


async def test_human_request_escalates_with_context_payload():
    """Regra §7.1 #1 + payload de transbordo §7.3."""
    orch = _new_orchestrator()
    await orch.greet()
    await orch.identify("111.444.777-35")

    result = await orch.handle_utterance("quero falar com uma pessoa")

    assert result.escalate is True
    assert result.escalation is not None
    assert result.escalation.subscriber is not None
    assert result.escalation.subscriber.id == "sub-001"
    assert result.protocol_number is not None


async def test_fin02_second_copy_flow():
    orch = _new_orchestrator()
    await orch.greet()
    await orch.identify("111.444.777-35")

    result = await orch.handle_utterance("Preciso da segunda via do boleto")

    assert result.escalate is False
    assert "R$" in result.text


async def test_unknown_intent_escalates_gracefully():
    orch = _new_orchestrator()
    await orch.greet()
    await orch.identify("111.444.777-35")

    result = await orch.handle_utterance("quero fazer um pedido de pizza")

    assert result.escalate is True
