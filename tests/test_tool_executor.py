"""Testes do ToolExecutor — loop real de tool-calling, sem chamada de rede
real: injeta um client Anthropic fake que devolve blocos `tool_use`/`text`
como o `messages.create()` real devolveria.
"""
import pytest

from voxisp.connectors.mock import MockISPConnector
from voxisp.fsm.states import CallState, Intent
from voxisp.orchestrator.tool_executor import ToolAllowlistViolation, ToolExecutor


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
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _FakeAnthropicClient:
    def __init__(self, responses: list[_FakeResponse]):
        self.messages = _FakeMessages(responses)

    def with_options(self, **kwargs):
        return self


async def _get_subscriber():
    connector = MockISPConnector()
    subscriber = await connector.find_subscriber(cpf="11144477735")
    assert subscriber is not None
    return connector, subscriber


async def test_run_executes_allowed_tool_and_stops_on_end_turn():
    connector, subscriber = await _get_subscriber()
    responses = [
        _FakeResponse([_FakeBlock("tool_use", id="call1", name="get_connection_status", input={})]),
        _FakeResponse([_FakeBlock("text", text="ok")]),
    ]
    executor = ToolExecutor(client=_FakeAnthropicClient(responses), connector=connector)

    result = await executor.run(
        subscriber=subscriber,
        intent=Intent.NET_01_SESSION_DIAGNOSIS,
        state=CallState.EXECUTION,
        instructions="diagnostique a conexão",
    )

    assert result.tool_calls == ["get_connection_status"]
    assert "get_connection_status" in result.diagnostics
    assert result.final_text == "ok"


async def test_run_raises_before_calling_llm_when_no_tools_allowed():
    connector, subscriber = await _get_subscriber()
    client = _FakeAnthropicClient([])  # não deveria nem ser consultado
    executor = ToolExecutor(client=client, connector=connector)

    with pytest.raises(ToolAllowlistViolation):
        await executor.run(
            subscriber=subscriber,
            intent=Intent.NET_01_SESSION_DIAGNOSIS,
            state=CallState.SLOT_COLLECTION,  # antes de EXECUTION — allowlist vazia
            instructions="...",
        )
    assert client.messages.calls == []


async def test_run_refuses_tool_outside_allowlist_defense_in_depth():
    """Mesmo que o client "hospede" uma tool fora do allowlist (simulando
    drift de SDK/versão), o executor nunca a executa."""
    connector, subscriber = await _get_subscriber()
    responses = [
        _FakeResponse([_FakeBlock("tool_use", id="call1", name="reboot_cpe", input={})]),
        _FakeResponse([_FakeBlock("text", text="ok")]),
    ]
    executor = ToolExecutor(client=_FakeAnthropicClient(responses), connector=connector)

    result = await executor.run(
        subscriber=subscriber,
        intent=Intent.NET_01_SESSION_DIAGNOSIS,  # allowlist não inclui reboot_cpe
        state=CallState.EXECUTION,
        instructions="...",
    )

    assert result.tool_calls == []
    assert "reboot_cpe" not in result.diagnostics


async def test_run_raises_timeout_when_loop_never_ends():
    connector, subscriber = await _get_subscriber()
    responses = [
        _FakeResponse([_FakeBlock("tool_use", id=f"call{i}", name="get_connection_status", input={})])
        for i in range(10)
    ]
    executor = ToolExecutor(client=_FakeAnthropicClient(responses), connector=connector, max_turns=2)

    with pytest.raises(TimeoutError):
        await executor.run(
            subscriber=subscriber,
            intent=Intent.NET_01_SESSION_DIAGNOSIS,
            state=CallState.EXECUTION,
            instructions="...",
        )


async def test_run_recovers_from_connector_error_without_crashing():
    connector, subscriber = await _get_subscriber()
    responses = [
        # issue_second_copy sem invoice_id válido -> erro do conector (KeyError)
        _FakeResponse([_FakeBlock("tool_use", id="call1", name="issue_second_copy", input={})]),
        _FakeResponse([_FakeBlock("text", text="não consegui emitir a segunda via")]),
    ]
    executor = ToolExecutor(client=_FakeAnthropicClient(responses), connector=connector)

    result = await executor.run(
        subscriber=subscriber,
        intent=Intent.FIN_02_SECOND_COPY,
        state=CallState.EXECUTION,
        instructions="...",
    )

    assert result.tool_calls == []  # a tentativa falhou, nunca contou como sucesso
    assert result.final_text == "não consegui emitir a segunda via"
