"""Interface de TTS (Text-to-Speech) — spec §4.2.

Candidatos de produção: ElevenLabs Flash, Cartesia Sonic, Azure Neural
(pt-BR-FranciscaNeural), Google Chirp3-HD. `StubTTS` só existe para dev/CI.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol


class TTSEngine(Protocol):
    """Síntese por sentença, streaming, cancelável no barge-in."""

    async def synthesize(self, text: str) -> AsyncIterator[bytes]: ...


class StubTTS:
    """Não sintetiza áudio de verdade — usado em dev/CI sem credenciais.
    Plugue um `ElevenLabsTTS`/`CartesiaTTS`/`AzureTTS` real antes de produção."""

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        if False:  # pragma: no cover - mantém a assinatura de async generator
            yield b""
