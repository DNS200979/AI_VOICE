"""Integração CallOrchestrator + ToolExecutor: prova que o fluxo NET-01
continua correto (mesmo resultado do §7.2) quando os diagnósticos vêm de um
loop real de tool-calling do Claude em vez de chamadas diretas ao conector.
"""
from voxisp.connectors.mock import MockISPConnector
from voxisp.connectors.models import SODraft
from voxisp.fsm.states import CallState
from voxisp.orchestrator.llm_client import StubLLMClient
from voxisp.orchestrator.tool_executor import ToolExecutor
from voxisp.orchestrator.turn_manager import CallOrchestrator


class _FakeBlock:
    def __init__(self, type_: str, **kwargs):
        self.type = type_
        for key, value in kwargs.items():
            setattr(self, key, value)


class _FakeResponse:
    def __init__(self, content: list[_FakeBlock]):
        self.content = content


class _FakeMessages:
    def __init__(self, responses: list[_FakeResponse]):
        self._responses = list(responses)

    async def create(self, **kwargs):
        return self._responses.pop(0)


class _FakeAnthropicClient:
    def __init__(self, responses: list[_FakeResponse]):
        self.messages = _FakeMessages(responses)

    def with_options(self, **kwargs):
        return self


async def test_net01_massive_flow_via_tool_executor_matches_direct_call_flow():
    connector = MockISPConnector()
    responses = [
        _FakeResponse([_FakeBlock("tool_use", id="c1", name="get_connection_status", input={})]),
        _FakeResponse([_FakeBlock("tool_use", id="c2", name="get_cpe_diagnostics", input={})]),
        _FakeResponse([_FakeBlock("text", text="ok")]),
    ]
    tool_executor = ToolExecutor(client=_FakeAnthropicClient(responses), connector=connector)

    orch = CallOrchestrator(connector=connector, llm=StubLLMClient(), tool_executor=tool_executor)
    await orch.greet()
    await orch.identify("111.444.777-35")  # sub-001: PON com massivo (§7.2)

    result = await orch.handle_utterance("Minha internet parou.")

    assert result.escalate is False
    assert result.protocol_number is not None
    assert "interrupção" in result.text.lower()
    assert "NET-01.get_connection_status(via-llm)" in orch._diagnostics
    assert "NET-01.get_cpe_diagnostics(via-llm)" in orch._diagnostics
    assert "NET-03.massive_check" in orch._diagnostics  # correlação de massivo continua determinística


async def test_net01_falls_back_to_direct_call_if_model_skips_the_tool():
    """Se o modelo não chamar get_connection_status, o orquestrador não
    segue sem o dado obrigatório — degrada para chamada direta ao conector."""
    connector = MockISPConnector()
    responses = [_FakeResponse([_FakeBlock("text", text="ok, sem chamar nenhuma tool")])]
    tool_executor = ToolExecutor(client=_FakeAnthropicClient(responses), connector=connector)

    orch = CallOrchestrator(connector=connector, llm=StubLLMClient(), tool_executor=tool_executor)
    await orch.greet()
    await orch.identify("111.444.777-35")

    result = await orch.handle_utterance("Minha internet parou.")

    assert result.escalate is False
    assert "NET-01.get_connection_status" in orch._diagnostics  # via fallback determinístico


async def test_fin02_flow_via_tool_executor():
    connector = MockISPConnector()
    responses = [
        _FakeResponse([_FakeBlock("tool_use", id="c1", name="get_invoices", input={"status": "open"})]),
        _FakeResponse(
            [_FakeBlock("tool_use", id="c2", name="issue_second_copy", input={"invoice_id": "inv-1001"})]
        ),
        _FakeResponse([_FakeBlock("text", text="ok")]),
    ]
    tool_executor = ToolExecutor(client=_FakeAnthropicClient(responses), connector=connector)

    orch = CallOrchestrator(connector=connector, llm=StubLLMClient(), tool_executor=tool_executor)
    await orch.greet()
    await orch.identify("111.444.777-35")

    result = await orch.handle_utterance("Preciso da segunda via do boleto")

    assert result.escalate is False
    assert "R$" in result.text
    assert "FIN-02.get_invoices(via-llm)" in orch._diagnostics
    assert "FIN-02.issue_second_copy(via-llm)" in orch._diagnostics


async def test_fin02_completes_second_copy_if_model_lists_but_skips_issuing():
    """O modelo consultou as faturas mas não emitiu a 2ª via — o fluxo
    completa via chamada direta em vez de responder sem o PIX/linha
    digitável (spec §12: cliente sempre recebe o que pediu, com dado real)."""
    connector = MockISPConnector()
    responses = [
        _FakeResponse([_FakeBlock("tool_use", id="c1", name="get_invoices", input={"status": "open"})]),
        _FakeResponse([_FakeBlock("text", text="há uma fatura em aberto")]),
    ]
    tool_executor = ToolExecutor(client=_FakeAnthropicClient(responses), connector=connector)

    orch = CallOrchestrator(connector=connector, llm=StubLLMClient(), tool_executor=tool_executor)
    await orch.greet()
    await orch.identify("111.444.777-35")

    result = await orch.handle_utterance("Preciso da segunda via do boleto")

    assert result.escalate is False
    assert "R$" in result.text
    assert "FIN-02.issue_second_copy" in orch._diagnostics  # sem sufixo "(via-llm)" -> veio do fallback


async def test_ops01_flow_via_tool_executor():
    connector = MockISPConnector()
    responses = [
        _FakeResponse([_FakeBlock("tool_use", id="c1", name="list_service_orders", input={})]),
        _FakeResponse([_FakeBlock("text", text="ok")]),
    ]
    tool_executor = ToolExecutor(client=_FakeAnthropicClient(responses), connector=connector)

    orch = CallOrchestrator(connector=connector, llm=StubLLMClient(), tool_executor=tool_executor)
    await orch.greet()
    await orch.identify("111.444.777-35")

    result = await orch.handle_utterance("quero saber o status da os")

    assert result.escalate is False
    assert "OPS-01.list_service_orders(via-llm)" in orch._diagnostics


async def test_fin03_confirmed_execution_via_tool_executor():
    """1ª passada (elegibilidade) é sempre determinística — a tool
    `request_trust_unlock` só entra no allowlist depois da confirmação
    verbal, quando a FSM alcança EXECUTION (spec §3.2/§4.3)."""
    connector = MockISPConnector()
    responses = [
        _FakeResponse([_FakeBlock("tool_use", id="c1", name="request_trust_unlock", input={})]),
        _FakeResponse([_FakeBlock("text", text="ok")]),
    ]
    tool_executor = ToolExecutor(client=_FakeAnthropicClient(responses), connector=connector)

    orch = CallOrchestrator(connector=connector, llm=StubLLMClient(), tool_executor=tool_executor)
    await orch.greet()
    await orch.identify("111.444.777-35")
    await orch.handle_utterance("quero o desbloqueio de confiança")

    result = await orch.handle_utterance("sim, confirmo")

    assert result.escalate is False
    assert "liberado" in result.text.lower()
    assert "FIN-03.request_trust_unlock(via-llm)" in orch._diagnostics


async def test_net04_confirmed_execution_via_tool_executor():
    connector = MockISPConnector()
    responses = [
        _FakeResponse([_FakeBlock("tool_use", id="c1", name="reboot_cpe", input={})]),
        _FakeResponse([_FakeBlock("text", text="ok")]),
    ]
    tool_executor = ToolExecutor(client=_FakeAnthropicClient(responses), connector=connector)

    orch = CallOrchestrator(connector=connector, llm=StubLLMClient(), tool_executor=tool_executor)
    await orch.greet()
    await orch.identify("111.444.777-35")
    await orch.handle_utterance("quero reiniciar o roteador")

    result = await orch.handle_utterance("sim")

    assert result.escalate is False
    assert "reinicialização" in result.text.lower()
    assert "NET-04.reboot_cpe(via-llm)" in orch._diagnostics


async def test_ops02_confirmed_execution_via_tool_executor():
    """OPS-02 usa o método dedicado `manage_visit` (não mais
    `create_service_order`) — a ação (agendar/reagendar/cancelar) é
    injetada por `extra_context`, nunca escolhida pelo modelo."""
    connector = MockISPConnector()
    responses = [
        _FakeResponse([_FakeBlock("tool_use", id="c1", name="manage_visit", input={})]),
        _FakeResponse([_FakeBlock("text", text="ok")]),
    ]
    tool_executor = ToolExecutor(client=_FakeAnthropicClient(responses), connector=connector)

    orch = CallOrchestrator(connector=connector, llm=StubLLMClient(), tool_executor=tool_executor)
    await orch.greet()
    await orch.identify("111.444.777-35")
    await orch.handle_utterance("preciso agendar visita técnica")

    result = await orch.handle_utterance("pode sim")

    assert result.escalate is False
    assert result.protocol_number is not None
    assert "OPS-02.manage_visit(via-llm)" in orch._diagnostics


async def test_ops02_reschedule_extracts_window_via_tool_executor():
    """Com tool_executor configurado, reagendamento não escalona — o
    modelo extrai window_start/window_end da fala do cliente."""
    connector = MockISPConnector()
    responses = [
        _FakeResponse(
            [
                _FakeBlock(
                    "tool_use",
                    id="c1",
                    name="manage_visit",
                    input={"window_start": "2026-09-01T14:00:00", "window_end": "2026-09-01T15:00:00"},
                )
            ]
        ),
        _FakeResponse([_FakeBlock("text", text="ok")]),
    ]
    tool_executor = ToolExecutor(client=_FakeAnthropicClient(responses), connector=connector)

    orch = CallOrchestrator(connector=connector, llm=StubLLMClient(), tool_executor=tool_executor)
    await orch.greet()
    await orch.identify("111.444.777-35")
    # reagendar exige uma OS já existente — manage_visit nunca cria uma do zero.
    await connector.create_service_order(
        SODraft(subscriber_id="sub-001", category="visita_tecnica", summary="Visita já agendada")
    )
    first = await orch.handle_utterance("quero reagendar visita para dia 1 de setembro às 14h")
    assert first.escalate is False
    assert orch.fsm.state == CallState.CONFIRMATION

    result = await orch.handle_utterance("sim")

    assert result.escalate is False
    assert result.protocol_number is not None
    assert "OPS-02.manage_visit(via-llm)" in orch._diagnostics


async def test_ops03_confirmed_execution_via_tool_executor():
    connector = MockISPConnector()
    responses = [
        _FakeResponse(
            [
                _FakeBlock(
                    "tool_use",
                    id="c1",
                    name="create_service_order",
                    input={"category": "suporte_tecnico", "summary": "Abertura de OS solicitada"},
                )
            ]
        ),
        _FakeResponse([_FakeBlock("text", text="ok")]),
    ]
    tool_executor = ToolExecutor(client=_FakeAnthropicClient(responses), connector=connector)

    orch = CallOrchestrator(connector=connector, llm=StubLLMClient(), tool_executor=tool_executor)
    await orch.greet()
    await orch.identify("111.444.777-35")
    await orch.handle_utterance("preciso abrir uma ordem de serviço")

    result = await orch.handle_utterance("confirmado")

    assert result.escalate is False
    assert result.protocol_number is not None
    assert "OPS-03.create_service_order(via-llm)" in orch._diagnostics
