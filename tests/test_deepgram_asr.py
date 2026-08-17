"""Testes do DeepgramASR — sem rede: injeta um `connect_fn` fake no lugar
de `websockets.connect`, simulando o WebSocket de streaming da Deepgram
(mensagens verificadas em developers.deepgram.com/reference/listen-live).
"""
import asyncio
import json

import pytest

from voxisp.config import Settings
from voxisp.voice.asr import DeepgramASR, DeepgramNotConfiguredError, get_asr_engine


class _FakeWebSocket:
    """Simula o fechamento real da Deepgram: a conexão só "encerra" (o
    `async for` do lado de recepção termina) depois de processar o
    `CloseStream` que `_pump_audio` manda — nunca antes, mesmo que não
    haja mais nenhuma mensagem programada. Sem isso, um fake que encerra a
    iteração de recepção antes do pump rodar cancelaria o envio de áudio
    ainda pendente (achado rodando os testes)."""

    def __init__(self, incoming_messages: list[str]):
        self._incoming = list(incoming_messages)
        self.sent: list[str | bytes] = []
        self._closed = asyncio.Event()

    async def send(self, data):
        self.sent.append(data)
        if isinstance(data, str) and json.loads(data).get("type") == "CloseStream":
            self._closed.set()

    def __aiter__(self):
        return self._iter_messages()

    async def _iter_messages(self):
        for message in self._incoming:
            yield message
        await self._closed.wait()


class _FakeConnectCM:
    def __init__(self, ws: _FakeWebSocket, *, captured: dict):
        self._ws = ws
        self._captured = captured

    async def __aenter__(self):
        return self._ws

    async def __aexit__(self, *exc_info):
        return False


def _results_message(transcript: str, *, is_final: bool, confidence: float = 0.98) -> str:
    return json.dumps(
        {
            "type": "Results",
            "is_final": is_final,
            "channel": {"alternatives": [{"transcript": transcript, "confidence": confidence}]},
        }
    )


async def _audio_frames(*frames: bytes):
    for frame in frames:
        yield frame


def test_requires_api_key():
    with pytest.raises(DeepgramNotConfiguredError):
        DeepgramASR("")


def test_url_uses_nova3_and_pt_br_by_default():
    ws = _FakeWebSocket([])
    captured: dict = {}

    def connect_fn(url, headers):
        captured["url"] = url
        captured["headers"] = headers
        return _FakeConnectCM(ws, captured=captured)

    asr = DeepgramASR("fake-key", connect_fn=connect_fn)
    assert "model=nova-3" in asr._url()
    assert "language=pt-BR" in asr._url()
    assert "sample_rate=8000" in asr._url()


async def test_stream_yields_only_results_with_transcript():
    messages = [
        _results_message("", is_final=False),  # silêncio — Deepgram manda, não deve virar ASRResult
        _results_message("oi", is_final=False, confidence=0.7),
        json.dumps({"type": "Metadata", "request_id": "abc"}),  # tipo irrelevante, ignorado
        _results_message("oi tudo bem", is_final=True, confidence=0.95),
    ]
    ws = _FakeWebSocket(messages)

    def connect_fn(url, headers):
        assert headers["Authorization"] == "Token fake-key"
        return _FakeConnectCM(ws, captured={})

    asr = DeepgramASR("fake-key", connect_fn=connect_fn)
    results = [r async for r in asr.stream(_audio_frames(b"frame1", b"frame2"))]

    assert [r.text for r in results] == ["oi", "oi tudo bem"]
    assert results[0].is_final is False
    assert results[1].is_final is True
    assert results[1].confidence == 0.95


async def test_stream_sends_audio_frames_then_close_stream():
    ws = _FakeWebSocket([])

    def connect_fn(url, headers):
        return _FakeConnectCM(ws, captured={})

    asr = DeepgramASR("fake-key", connect_fn=connect_fn)
    async for _ in asr.stream(_audio_frames(b"frame1", b"frame2")):
        pass

    assert ws.sent[0] == b"frame1"
    assert ws.sent[1] == b"frame2"
    assert json.loads(ws.sent[2]) == {"type": "CloseStream"}


def test_get_asr_engine_factory_stub():
    from voxisp.voice.asr import StubASR

    assert isinstance(get_asr_engine("stub"), StubASR)


def test_get_asr_engine_factory_deepgram():
    settings = Settings(asr_provider="deepgram", asr_api_key="fake-key")
    engine = get_asr_engine("deepgram", settings)
    assert isinstance(engine, DeepgramASR)


def test_get_asr_engine_factory_unknown_raises():
    with pytest.raises(ValueError, match="desconhecido"):
        get_asr_engine("whisper")
