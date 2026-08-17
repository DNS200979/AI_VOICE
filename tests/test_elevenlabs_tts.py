"""Testes do ElevenLabsTTS — sem rede: usa `httpx.MockTransport` simulando
o endpoint real de streaming (verificado em
elevenlabs.io/docs/api-reference/text-to-speech/stream).
"""
import json as _json

import httpx
import pytest

from voxisp.config import Settings
from voxisp.voice.tts import ElevenLabsNotConfiguredError, ElevenLabsTTS, TTSError, get_tts_engine


def _adapter_with(handler) -> ElevenLabsTTS:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return ElevenLabsTTS("fake-key", "voice-123", client=client)


def test_requires_api_key_and_voice_id():
    with pytest.raises(ElevenLabsNotConfiguredError):
        ElevenLabsTTS("", "voice-123")
    with pytest.raises(ElevenLabsNotConfiguredError):
        ElevenLabsTTS("fake-key", "")


async def test_synthesize_calls_correct_endpoint_and_streams_chunks():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/text-to-speech/voice-123/stream"
        assert request.url.params["output_format"] == "alaw_8000"
        assert request.headers["xi-api-key"] == "fake-key"
        body = _json.loads(request.read())
        assert body["text"] == "Olá, tudo bem?"
        assert body["model_id"] == "eleven_flash_v2_5"
        assert body["language_code"] == "pt"
        return httpx.Response(200, content=b"\x01\x02\x03\x04" * 100)

    adapter = _adapter_with(handler)
    chunks = [chunk async for chunk in adapter.synthesize("Olá, tudo bem?")]

    assert b"".join(chunks) == b"\x01\x02\x03\x04" * 100


async def test_synthesize_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, content=b'{"detail": "invalid voice_id"}')

    adapter = _adapter_with(handler)
    with pytest.raises(TTSError, match="422"):
        async for _ in adapter.synthesize("teste"):
            pass


def test_get_tts_engine_factory_stub():
    from voxisp.voice.tts import StubTTS

    assert isinstance(get_tts_engine("stub"), StubTTS)


def test_get_tts_engine_factory_elevenlabs():
    settings = Settings(tts_provider="elevenlabs", tts_api_key="fake-key", tts_voice_id="voice-123")
    engine = get_tts_engine("elevenlabs", settings)
    assert isinstance(engine, ElevenLabsTTS)


def test_get_tts_engine_factory_unknown_raises():
    with pytest.raises(ValueError, match="desconhecido"):
        get_tts_engine("cartesia")
