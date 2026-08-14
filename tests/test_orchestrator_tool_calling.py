"""Integração CallOrchestrator + ToolExecutor: prova que o fluxo NET-01
continua correto (mesmo resultado do §7.2) quando os diagnósticos vêm de um
loop real de tool-calling do Claude em vez de chamadas diretas ao conector.
"""
from voxisp.connectors.mock import MockISPConnector
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
