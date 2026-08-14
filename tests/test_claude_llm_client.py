"""Testes do ClaudeLLMClient — sem chamada de rede real: injeta um client
Anthropic fake que imita a forma de `AsyncAnthropic().with_options().messages.parse()`.
"""
from voxisp.config import Settings
from voxisp.fsm.states import Intent
from voxisp.orchestrator.llm_client import (
    ClaudeLLMClient,
    IntentClassificationSchema,
    get_llm_client,
)


class _FakeParsedResponse:
    def __init__(self, parsed_output):
        self.parsed_output = parsed_output


class _FakeMessages:
    def __init__(self, parsed_output=None, exc: Exception | None = None):
        self._parsed_output = parsed_output
        self._exc = exc

    async def parse(self, **kwargs):
        if self._exc is not None:
            raise self._exc
        return _FakeParsedResponse(self._parsed_output)


class _FakeAnthropicClient:
    """Imita a superfície mínima de `anthropic.AsyncAnthropic` usada aqui."""

    def __init__(self, parsed_output=None, exc: Exception | None = None):
        self.messages = _FakeMessages(parsed_output, exc)

    def with_options(self, **kwargs):
        return self


async def test_classify_intent_happy_path():
    schema = IntentClassificationSchema(
        intent=Intent.FIN_02_SECOND_COPY, entities={}, confidence=0.95
    )
    client = ClaudeLLMClient(client=_FakeAnthropicClient(parsed_output=schema))

    result = await client.classify_intent("quero a segunda via do boleto", context={})

    assert result.intent == Intent.FIN_02_SECOND_COPY
    assert result.confidence == 0.95


async def test_classify_intent_degrades_to_unknown_on_error():
    """Timeout duro ou erro de API (spec §4.3) nunca deve propagar — degrada
    para UNKNOWN, que o orquestrador trata como transbordo."""
    client = ClaudeLLMClient(client=_FakeAnthropicClient(exc=TimeoutError("simulated timeout")))

    result = await client.classify_intent("qualquer coisa", context={})

    assert result.intent == Intent.UNKNOWN
    assert result.confidence == 0.0


async def test_draft_turn_never_calls_the_llm():
    """Regra §12: valores financeiros vêm de template travado, nunca de
    geração livre do LLM — mesmo comportamento do StubLLMClient."""
    client = ClaudeLLMClient(client=_FakeAnthropicClient())

    text = await client.draft_turn("Valor: R$ {amount}", {"amount": "10,00"})

    assert text == "Valor: R$ 10,00"


def test_factory_wires_claude_llm_client():
    settings = Settings(llm_api_key="test-key-not-real")
    client = get_llm_client("anthropic", settings=settings)
    assert isinstance(client, ClaudeLLMClient)


def test_factory_unknown_provider_raises():
    import pytest

    with pytest.raises(ValueError, match="desconhecido"):
        get_llm_client("openai")
