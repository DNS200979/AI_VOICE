"""Testes do protocolo AudioSocket (`telephony/audio_bridge.py`) —
verificado contra docs.asterisk.org/Configuration/Channel-Drivers/AudioSocket/
e, à parte destes testes unitários, validado de ponta a ponta contra um
Asterisk real via Docker (ver docs/telephony/audiosocket.md).
"""
import asyncio
import uuid

import pytest

from voxisp.telephony.audio_bridge import AudioSocketBridge, AudioSocketError, AudioSocketServer


class _FakeWriter:
    def __init__(self):
        self.written = b""
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written += data

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        pass


def _reader_with(*messages: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    for message in messages:
        reader.feed_data(message)
    reader.feed_eof()
    return reader


def _audio_message(payload: bytes) -> bytes:
    return bytes([0x10]) + len(payload).to_bytes(2, "big") + payload


def _dtmf_message(digit: str) -> bytes:
    payload = digit.encode("ascii")
    return bytes([0x03]) + len(payload).to_bytes(2, "big") + payload


def _uuid_message(call_uuid: uuid.UUID) -> bytes:
    payload = call_uuid.bytes
    return bytes([0x01]) + len(payload).to_bytes(2, "big") + payload


def _terminate_message() -> bytes:
    return bytes([0x00, 0x00, 0x00])


def _error_message(text: bytes) -> bytes:
    return bytes([0xFF]) + len(text).to_bytes(2, "big") + text


async def test_receive_frames_yields_only_audio_payload():
    reader = _reader_with(_audio_message(b"\x01\x02"), _audio_message(b"\x03\x04"), _terminate_message())
    bridge = AudioSocketBridge(reader, _FakeWriter())

    frames = [f async for f in bridge.receive_frames()]

    assert frames == [b"\x01\x02", b"\x03\x04"]


async def test_receive_frames_captures_uuid_and_dtmf_without_yielding_them():
    call_id = uuid.uuid4()
    reader = _reader_with(
        _uuid_message(call_id),
        _dtmf_message("5"),
        _audio_message(b"\x00\x00"),
        _dtmf_message("#"),
        _terminate_message(),
    )
    bridge = AudioSocketBridge(reader, _FakeWriter())

    frames = [f async for f in bridge.receive_frames()]

    assert frames == [b"\x00\x00"]
    assert bridge.call_uuid == str(call_id)
    assert bridge.dtmf_digits == ["5", "#"]


async def test_receive_frames_raises_on_error_message():
    reader = _reader_with(_error_message(b"bad codec"))
    bridge = AudioSocketBridge(reader, _FakeWriter())

    with pytest.raises(AudioSocketError, match="bad codec"):
        async for _ in bridge.receive_frames():
            pass


async def test_receive_frames_ends_gracefully_on_dropped_connection():
    """Conexão cai sem um terminate explícito (ex.: rede caiu) — não pode
    propagar um erro de framing bruto, só encerrar a iteração."""
    reader = _reader_with(_audio_message(b"\x01\x02"))  # sem terminate — EOF no meio de um header depois

    bridge = AudioSocketBridge(reader, _FakeWriter())
    frames = [f async for f in bridge.receive_frames()]

    assert frames == [b"\x01\x02"]


async def test_send_frame_writes_correct_header():
    writer = _FakeWriter()
    bridge = AudioSocketBridge(asyncio.StreamReader(), writer)

    await bridge.send_frame(b"\x01\x02\x03")

    assert writer.written == bytes([0x10, 0x00, 0x03]) + b"\x01\x02\x03"


async def test_hangup_sends_terminate_and_closes():
    writer = _FakeWriter()
    bridge = AudioSocketBridge(asyncio.StreamReader(), writer)

    await bridge.hangup()

    assert writer.written == bytes([0x00, 0x00, 0x00])
    assert writer.closed is True


async def test_transfer_not_implemented():
    bridge = AudioSocketBridge(asyncio.StreamReader(), _FakeWriter())
    with pytest.raises(NotImplementedError, match="ARI"):
        await bridge.transfer("fila-humana")


# -- Round-trip real via TCP loopback local (sem Asterisk) -------------------


async def test_audio_socket_server_round_trip_over_real_tcp():
    """Sobe um AudioSocketServer de verdade num socket TCP local e conecta
    nele com um client TCP de verdade — exercita o framing real por cima
    de I/O de rede real (loopback), não só objetos fake em memória."""
    received: list[bytes] = []
    handled = asyncio.Event()

    async def on_connect(bridge: AudioSocketBridge) -> None:
        async for frame in bridge.receive_frames():
            received.append(frame)
        await bridge.send_frame(b"resposta")
        handled.set()

    server = AudioSocketServer("127.0.0.1", 0, on_connect)
    await server.start()
    port = server._server.sockets[0].getsockname()[1]

    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(_audio_message(b"ola"))
        writer.write(_terminate_message())
        await writer.drain()

        await asyncio.wait_for(handled.wait(), timeout=2)

        header = await reader.readexactly(3)
        length = int.from_bytes(header[1:3], "big")
        payload = await reader.readexactly(length)
        assert payload == b"resposta"

        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()

    assert received == [b"ola"]
