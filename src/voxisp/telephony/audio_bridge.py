"""Ponte com a borda telefônica — spec §3.

Produção: SIP Trunk -> Kamailio (SBC) -> Asterisk 20, ligado ao voice
runtime via AudioSocket (implementado abaixo) ou ARI externalMedia (não
implementado). `AudioBridge` é o contrato abstrato; `AudioSocketBridge`/
`AudioSocketServer` são a implementação real contra o protocolo
`app_audiosocket` do próprio Asterisk — verificado e testado contra um
Asterisk real (Docker), ver docs/telephony/audiosocket.md.
"""
from __future__ import annotations

import asyncio
import contextlib
import uuid as uuid_module
from collections.abc import AsyncIterator, Awaitable, Callable
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


class AudioSocketError(Exception):
    """O Asterisk reportou um erro pelo protocolo AudioSocket (tipo 0xff)."""


class AudioSocketBridge:
    """`AudioBridge` real contra o protocolo AudioSocket do Asterisk
    (`app_audiosocket`/`res_audiosocket`) — spec §3.

    Protocolo verificado em
    docs.asterisk.org/Configuration/Channel-Drivers/AudioSocket/ e testado
    de ponta a ponta contra um Asterisk real (Docker, `Originate` +
    dialplan mínimo — ver docs/telephony/audiosocket.md):

    - Cabeçalho de 3 bytes por mensagem: 1 byte de tipo + 2 bytes de
      tamanho do payload (uint16 **big-endian**).
    - Tipos usados aqui: `0x00` terminate, `0x01` UUID (16 bytes binários
      do identificador da chamada), `0x03` DTMF (1 byte ASCII — spec
      §4.1: fallback de DTMF para CPF após falha de reconhecimento),
      `0x10` áudio PCM signed-linear 16-bit **little-endian** mono a
      8kHz, `0xff` erro. Tipos de áudio em outra taxa (`0x11`/`0x12`/`0x16`
      — 12/16/48kHz) são ignorados: a stack inteira assume 8kHz (spec §3).
    - `hangup()` manda um `terminate` (tipo `0x00`, payload vazio) antes
      de fechar o socket — a doc confirma que só fechar o socket também
      basta, mas mandar o terminate é mais explícito para os logs do
      Asterisk.
    """

    _TERMINATE = 0x00
    _UUID = 0x01
    _DTMF = 0x03
    _AUDIO_8KHZ = 0x10
    _ERROR = 0xFF

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, call_uuid: str | None = None) -> None:
        self._reader = reader
        self._writer = writer
        self.call_uuid = call_uuid
        # Dígitos DTMF acumulados nesta conexão — spec §4.1: fallback de
        # CPF via teclado após 1 falha de reconhecimento de voz. Quem
        # consome (voice runtime) decide quando/como usar isso.
        self.dtmf_digits: list[str] = []

    async def receive_frames(self) -> AsyncIterator[bytes]:
        while True:
            try:
                header = await self._reader.readexactly(3)
            except (asyncio.IncompleteReadError, ConnectionResetError):
                # Conexão caiu sem um terminate explícito — trata como fim
                # de chamada em vez de propagar o erro de framing bruto.
                return
            kind = header[0]
            length = int.from_bytes(header[1:3], "big")
            payload = await self._reader.readexactly(length) if length else b""

            if kind == self._TERMINATE:
                return
            if kind == self._AUDIO_8KHZ:
                yield payload
            elif kind == self._DTMF:
                self.dtmf_digits.append(payload.decode("ascii", errors="ignore"))
            elif kind == self._UUID:
                self.call_uuid = str(uuid_module.UUID(bytes=payload))
            elif kind == self._ERROR:
                raise AudioSocketError(f"AudioSocket reportou erro: {payload!r}")
            # Tipos de áudio em outra taxa (0x11/0x12/0x16) e quaisquer
            # outros tipos desconhecidos: ignorados de propósito.

    async def send_frame(self, frame: bytes) -> None:
        header = bytes([self._AUDIO_8KHZ]) + len(frame).to_bytes(2, "big")
        self._writer.write(header + frame)
        await self._writer.drain()

    async def hangup(self) -> None:
        self._writer.write(bytes([self._TERMINATE, 0x00, 0x00]))
        await self._writer.drain()
        self._writer.close()
        with contextlib.suppress(Exception):
            await self._writer.wait_closed()

    async def transfer(self, destination: str) -> None:
        # Confirmado, não é omissão: o protocolo AudioSocket em si não tem
        # uma mensagem de "transfira esta chamada". Transbordo de verdade
        # (spec §7.3) precisa de ARI channel redirect no canal Asterisk
        # associado a este `call_uuid` — uma API totalmente separada
        # (HTTP/WebSocket ARI, não o socket TCP do AudioSocket), fora do
        # escopo deste protocolo. Ver docs/telephony/audiosocket.md.
        raise NotImplementedError(
            "AudioSocketBridge.transfer(): protocolo AudioSocket não suporta "
            "transbordo — precisa de ARI channel redirect (não implementado). "
            "Ver docs/telephony/audiosocket.md."
        )


class AudioSocketServer:
    """Servidor TCP que aceita conexões do `app_audiosocket` do Asterisk —
    uma por chamada — e entrega um `AudioSocketBridge` para `on_connect`.

    `on_connect` roda até a chamada terminar (normalmente é o voice
    runtime completo — ver `voice/runtime.py`); quando ele retorna (ou
    levanta), a conexão é fechada.
    """

    def __init__(
        self,
        host: str,
        port: int,
        on_connect: Callable[[AudioSocketBridge], Awaitable[None]],
    ) -> None:
        self._host = host
        self._port = port
        self._on_connect = on_connect
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_connection, self._host, self._port)

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        bridge = AudioSocketBridge(reader, writer)
        try:
            await self._on_connect(bridge)
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

