"""Fluxo de confirmação de ação destrutiva (FIN-03) — spec §7.1 regra #2:
"Ação destrutiva ... exige confirmação verbal explícita". Cobre o caminho
sem `tool_executor` (chamada direta ao conector); o caminho com
tool-calling real está em `test_orchestrator_tool_calling.py`.
"""
from voxisp.connectors.mock import MockISPConnector
from voxisp.connectors.models import SODraft
from voxisp.fsm.states import CallState
from voxisp.orchestrator.llm_client import StubLLMClient
from voxisp.orchestrator.turn_manager import CallOrchestrator


def _new_orchestrator() -> CallOrchestrator:
    return CallOrchestrator(connector=MockISPConnector(), llm=StubLLMClient())


async def _start_fin03(orch: CallOrchestrator):
    await orch.greet()
    await orch.identify("111.444.777-35")  # sub-001: tem fatura em aberto -> elegível
    return await orch.handle_utterance("quero o desbloqueio de confiança")


async def test_confirmation_requested_before_executing_destructive_action():
    orch = _new_orchestrator()
    result = await _start_fin03(orch)

    assert result.escalate is False
    assert "confirma" in result.text.lower()
    assert orch.fsm.state == CallState.CONFIRMATION
    assert orch.fsm.pending_confirmation is True


async def test_yes_confirmation_executes_the_action():
    orch = _new_orchestrator()
    await _start_fin03(orch)

    result = await orch.handle_utterance("sim, confirmo")

    assert result.escalate is False
    assert "liberado" in result.text.lower()
    assert orch.fsm.state == CallState.CLOSING
    assert orch.fsm.pending_confirmation is False


async def test_no_confirmation_does_not_execute_the_action():
    orch = _new_orchestrator()
    await _start_fin03(orch)

    result = await orch.handle_utterance("não, deixa pra lá")

    assert result.escalate is False
    assert "não vou realizar" in result.text.lower()
    assert orch.fsm.state == CallState.INTENT_ROUTING


async def test_ambiguous_confirmation_asks_again_then_escalates():
    orch = _new_orchestrator()
    await _start_fin03(orch)

    result1 = await orch.handle_utterance("talvez")
    assert result1.escalate is False
    assert "não entendi" in result1.text.lower()
    assert orch.fsm.state == CallState.CONFIRMATION

    # 2ª falha de reconhecimento no mesmo slot -> transbordo (regra §7.1 #3)
    result2 = await orch.handle_utterance("hmm")
    assert result2.escalate is True


async def test_human_request_still_wins_during_confirmation():
    """Regra #1 do §7.1: "atendente" transborda em QUALQUER estado,
    inclusive aguardando confirmação de uma ação destrutiva."""
    orch = _new_orchestrator()
    await _start_fin03(orch)

    result = await orch.handle_utterance("quero falar com atendente")

    assert result.escalate is True


# -- NET-04 (reboot remoto) ---------------------------------------------


async def _start_net04(orch: CallOrchestrator):
    await orch.greet()
    await orch.identify("111.444.777-35")  # sub-001: tem cpe_serial cadastrado
    return await orch.handle_utterance("quero reiniciar o roteador")


async def test_net04_asks_confirmation_before_rebooting():
    orch = _new_orchestrator()
    result = await _start_net04(orch)

    assert result.escalate is False
    assert "confirma" in result.text.lower()
    assert orch.fsm.state == CallState.CONFIRMATION


async def test_net04_yes_confirmation_reboots():
    orch = _new_orchestrator()
    await _start_net04(orch)

    result = await orch.handle_utterance("sim")

    assert result.escalate is False
    assert "reinicialização" in result.text.lower()
    assert orch.fsm.state == CallState.CLOSING


async def test_net04_no_confirmation_does_not_reboot():
    orch = _new_orchestrator()
    await _start_net04(orch)

    result = await orch.handle_utterance("não")

    assert result.escalate is False
    assert "não vou realizar" in result.text.lower()


async def test_net04_without_cpe_serial_escalates_without_asking_confirmation():
    """Sem CPE cadastrado não há o que reiniciar — regra geral do
    orquestrador (quando não consegue completar o pedido, escalona)."""
    orch = _new_orchestrator()
    await orch.greet()
    await orch.identify("111.444.777-35")
    assert orch.subscriber is not None
    # model_copy — nunca mutar o objeto do mock in-place: ele é compartilhado
    # (mesma instância) entre chamadas/testes, e um cpe_serial=None aqui
    # vazaria para outros testes que também usam este assinante.
    orch.subscriber = orch.subscriber.model_copy(update={"cpe_serial": None})

    result = await orch.handle_utterance("quero reiniciar o roteador")

    assert result.escalate is True
    assert orch.fsm.state != CallState.CONFIRMATION


# -- OPS-02 (agendamento de visita) --------------------------------------


async def test_ops02_confirmation_flow_schedules_visit():
    orch = _new_orchestrator()
    await orch.greet()
    await orch.identify("111.444.777-35")
    first = await orch.handle_utterance("preciso agendar visita técnica")
    assert first.escalate is False
    assert orch.fsm.state == CallState.CONFIRMATION

    result = await orch.handle_utterance("pode sim")

    assert result.escalate is False
    assert result.protocol_number is not None
    assert "protocolo" in result.text.lower()
    assert orch.fsm.state == CallState.CLOSING


async def test_ops02_cancel_confirmation_flow():
    """Cancelamento (spec §2.1) reutiliza a OS já aberta em vez de criar
    uma nova — `manage_visit(action=cancel)` exige `service_order_id`."""
    connector = MockISPConnector()
    orch = CallOrchestrator(connector=connector, llm=StubLLMClient())
    await orch.greet()
    await orch.identify("111.444.777-35")
    await connector.create_service_order(
        SODraft(subscriber_id="sub-001", category="visita_tecnica", summary="Visita já agendada")
    )

    first = await orch.handle_utterance("quero desmarcar a visita técnica")
    assert first.escalate is False
    assert orch.fsm.state == CallState.CONFIRMATION

    result = await orch.handle_utterance("sim")

    assert result.escalate is False
    assert result.protocol_number is not None
    assert orch.fsm.state == CallState.CLOSING


async def test_ops02_cancel_without_existing_order_escalates():
    orch = _new_orchestrator()
    await orch.greet()
    await orch.identify("111.444.777-35")

    result = await orch.handle_utterance("quero desmarcar a visita técnica")

    assert result.escalate is True
    assert orch.fsm.state != CallState.CONFIRMATION


async def _seed_open_visit(connector: MockISPConnector) -> None:
    await connector.create_service_order(
        SODraft(subscriber_id="sub-001", category="visita_tecnica", summary="Visita já agendada")
    )


async def test_ops02_reschedule_with_date_in_first_utterance_skips_followup():
    """Sem `tool_executor`, o parser determinístico (pt_datetime.py) tenta
    achar a data já na fala que disparou o intent — se achar, não precisa
    perguntar de novo."""
    connector = MockISPConnector()
    await _seed_open_visit(connector)
    orch = CallOrchestrator(connector=connector, llm=StubLLMClient())
    await orch.greet()
    await orch.identify("111.444.777-35")

    result = await orch.handle_utterance("quero reagendar visita para amanhã de manhã")

    assert result.escalate is False
    assert orch.fsm.state == CallState.CONFIRMATION
    assert "confirma" in result.text.lower()


async def test_ops02_reschedule_without_date_asks_followup_then_confirms():
    """Sem data na fala inicial, pergunta separado (`_awaiting_visit_window`)
    e a resposta seguinte não é reclassificada como intenção nova."""
    connector = MockISPConnector()
    await _seed_open_visit(connector)
    orch = CallOrchestrator(connector=connector, llm=StubLLMClient())
    await orch.greet()
    await orch.identify("111.444.777-35")

    first = await orch.handle_utterance("quero reagendar visita")
    assert first.escalate is False
    assert orch.fsm.state != CallState.CONFIRMATION
    assert orch._awaiting_visit_window is True

    second = await orch.handle_utterance("pode ser amanhã de manhã")
    assert second.escalate is False
    assert orch.fsm.state == CallState.CONFIRMATION
    assert orch._awaiting_visit_window is False

    result = await orch.handle_utterance("sim")
    assert result.escalate is False
    assert result.protocol_number is not None
    assert orch.fsm.state == CallState.CLOSING


async def test_ops02_reschedule_followup_unparseable_escalates_after_max_failures():
    """Regra §7.1 #3: falha de reconhecimento repetida no slot escalona,
    não fica perguntando pra sempre."""
    connector = MockISPConnector()
    await _seed_open_visit(connector)
    orch = CallOrchestrator(connector=connector, llm=StubLLMClient())
    await orch.greet()
    await orch.identify("111.444.777-35")

    await orch.handle_utterance("quero reagendar visita")
    first_retry = await orch.handle_utterance("sei lá, qualquer hora")
    assert first_retry.escalate is False

    result = await orch.handle_utterance("sei lá, qualquer hora de novo")
    assert result.escalate is True


async def test_ops02_reschedule_without_existing_order_escalates():
    orch = _new_orchestrator()
    await orch.greet()
    await orch.identify("111.444.777-35")

    result = await orch.handle_utterance("quero reagendar visita para amanhã de manhã")

    assert result.escalate is True
    assert orch.fsm.state != CallState.CONFIRMATION


# -- OPS-03 (abertura de OS) ----------------------------------------------


async def test_ops03_confirmation_flow_creates_service_order():
    orch = _new_orchestrator()
    await orch.greet()
    await orch.identify("111.444.777-35")
    first = await orch.handle_utterance("preciso abrir uma ordem de serviço")
    assert first.escalate is False
    assert orch.fsm.state == CallState.CONFIRMATION

    result = await orch.handle_utterance("confirmado")

    assert result.escalate is False
    assert result.protocol_number is not None
    assert orch.fsm.state == CallState.CLOSING
