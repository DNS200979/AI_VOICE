"""Interface de ASR (Speech-to-Text) — spec §4.1.

Candidatos de produção: Deepgram Nova, Azure Speech, NVIDIA Riva/Parakeet,
Whisper large-v3 fine-tuned. Nenhum é chamado aqui: esta é a fronteira
que qualquer um deles implementa. `StubASR` existe só para permitir rodar
o pipeline em dev/CI sem credenciais de nuvem.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ASRResult:
    text: str
    is_final: bool
    confidence: float


class ASREngine(Protocol):
    """Contrato de streaming: recebe frames PCM 8kHz, produz resultados parciais/finais."""

    async def stream(self, audio_frames: AsyncIterator[bytes]) -> AsyncIterator[ASRResult]: ...


class StubASR:
    """Implementação de desenvolvimento: não reconhece áudio de verdade.

    Útil para testar a FSM/orquestrador via texto direto (bypassa o
    reconhecimento). Plugue `DeepgramASR`/`AzureASR`/`RivaASR` reais
    seguindo o mesmo `Protocol` antes de ir a produção.
    """

    async def stream(self, audio_frames: AsyncIterator[bytes]) -> AsyncIterator[ASRResult]:
        async for _ in audio_frames:
            yield ASRResult(text="", is_final=False, confidence=0.0)
