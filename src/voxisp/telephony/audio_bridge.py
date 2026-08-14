"""Ponte com a borda telefônica — spec §3.

Produção: SIP Trunk -> Kamailio (SBC) -> Asterisk 20, ligado ao voice
runtime (LiveKit Agents ou Pipecat) via AudioSocket ou ARI externalMedia
(PCM 8kHz). Este módulo é apenas o contrato dessa ponte — a implementação
real de transporte de áudio fica fora do escopo deste repositório inicial
e deve ser resolvida junto da escolha de voice runtime (Fase 1/2).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol


class AudioBridge(Protocol):
    """Abstrai a origem/destino de áudio PCM 8kHz de uma chamada."""

    async def receive_frames(self) -> AsyncIterator[bytes]: ...

    async def send_frame(self, frame: bytes) -> None: ...

    async def hangup(self) -> None: ...

    async def transfer(self, destination: str) -> None:
        """Transbordo para fila humana (ESC-01..06) — implementado via
        ARI channel redirect no Asterisk."""
        ...
