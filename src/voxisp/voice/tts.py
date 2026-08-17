"""Interface de TTS (Text-to-Speech) — spec §4.2.

Candidatos de produção: ElevenLabs Flash (a spec lista primeiro, e é o
único implementado de verdade aqui) · Cartesia Sonic · Azure Neural
(`pt-BR-FranciscaNeural` etc.) · Google Chirp3-HD. `StubTTS` existe para
dev/CI sem credenciais de nuvem.

`ElevenLabsTTS` — endpoint e formato verificados em:
- https://elevenlabs.io/docs/api-reference/text-to-speech/stream
Ver docs/voice/elevenlabs.md para o mapeamento completo e limitações.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

import httpx


class TTSEngine(Protocol):
    """Síntese por sentença, streaming, cancelável no barge-in."""

    async def synthesize(self, text: str) -> AsyncIterator[bytes]: ...


class StubTTS:
    """Não sintetiza áudio de verdade — usado em dev/CI sem credenciais.
    Plugue um `ElevenLabsTTS`/`CartesiaTTS`/`AzureTTS` real antes de produção."""

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        if False:  # pragma: no cover - mantém a assinatura de async generator
            yield b""


class ElevenLabsNotConfiguredError(Exception):
    """`TTS_API_KEY`/`TTS_VOICE_ID` ausentes do `.env` (com `TTS_PROVIDER=elevenlabs`)."""


class TTSError(Exception):
    """Erro de conector TTS (rede, timeout, resposta de erro da API)."""


class ElevenLabsTTS:
    """TTS real via streaming HTTP da ElevenLabs (modelo Flash) — spec §4.2.

    Particularidades reais que moldaram este código:

    - `voice_id` **não tem default genérico possível** — é uma voz
      específica da conta ElevenLabs do provedor (clonada ou escolhida na
      biblioteca deles), nunca um valor universal. `TTS_VOICE_ID` no
      `.env` é obrigatório, sem fallback inventado.
    - `output_format=alaw_8000` por padrão — G.711 A-law 8kHz, o mesmo
      codec de telefonia usado no Brasil (spec §3: "Codec G.711a / Opus"),
      para não exigir um resample extra na borda telefônica. A API também
      aceita `ulaw_8000` (G.711 µ-law, padrão América do Norte/Japão) e
      variantes PCM/MP3/Opus — configurável se a ponte de áudio real usar
      outro codec.
    - `model_id="eleven_flash_v2_5"` — o modelo Flash citado na spec,
      otimizado para menor time-to-first-byte (spec §4.2: alvo ≤200ms),
      não o `eleven_multilingual_v2` (default da API, mais lento).
    - Resposta é o áudio bruto em streaming via chunked transfer encoding
      — sem framing/protocolo próprio, só bytes do codec pedido em
      `output_format`. `synthesize` repassa os chunks como chegam, sem
      bufferizar tudo antes de render (spec §4.2: streaming obrigatório).
    """

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        *,
        model_id: str = "eleven_flash_v2_5",
        language_code: str = "pt",
        output_format: str = "alaw_8000",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key or not voice_id:
            raise ElevenLabsNotConfiguredError(
                "TTS_API_KEY/TTS_VOICE_ID não configurados. Ver docs/voice/elevenlabs.md."
            )
        self._api_key = api_key
        self._voice_id = voice_id
        self._model_id = model_id
        self._language_code = language_code
        self._output_format = output_format
        self._client = client or httpx.AsyncClient()

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self._voice_id}/stream"
        headers = {"xi-api-key": self._api_key, "Content-Type": "application/json"}
        body: dict[str, Any] = {
            "text": text,
            "model_id": self._model_id,
            "language_code": self._language_code,
        }
        async with self._client.stream(
            "POST", url, params={"output_format": self._output_format}, headers=headers, json=body
        ) as response:
            if response.status_code >= 400:
                error_body = await response.aread()
                raise TTSError(
                    f"ElevenLabs TTS: HTTP {response.status_code} — {error_body[:300]!r}"
                )
            async for chunk in response.aiter_bytes():
                yield chunk


def get_tts_engine(provider: str = "stub", settings: Any = None) -> TTSEngine:
    """Fábrica de motores de TTS — mesmo padrão de `get_llm_client`/`get_connector`."""
    if provider == "stub":
        return StubTTS()
    if provider == "elevenlabs":
        if settings is None:
            from voxisp.config import settings as default_settings

            settings = default_settings
        return ElevenLabsTTS(
            api_key=settings.tts_api_key,
            voice_id=settings.tts_voice_id,
            output_format=settings.tts_output_format,
        )
    raise ValueError(f"TTS_PROVIDER '{provider}' desconhecido. Disponíveis: stub, elevenlabs.")
