"""Testes do voice runtime (`voice/runtime.py`) — liga AudioBridge/ASR/TTS
fake a um `CallOrchestrator` real (Mock + Stub, mesmo padrão dos testes de
orquestrador) para validar o loop de principio a fim sem rede nenhuma.
"""
import asyncio

from voxisp.connectors.mock import MockISPConnector
from voxisp.orchestrator.llm_client import StubLLMClient
from voxisp.orchestrator.turn_manager import CallOrchestrator
from voxisp.voice.asr import ASRResult
from voxisp.voice.runtime import run_call


class _FakeAudioBridge:
    def __init__(self, *, transfer_raises: bool = True):
        self.sent_frames: list[bytes] = []
        self.hangup_called = False
        self.transfer_calls: list[str] = []
        self._transfer_raises = transfer_raises

    async def receive_frames(self):
        # O conteúdo não importa — o _FakeASR ignora o áudio de entrada e
        # devolve resultados pré-programados.
        yield b"\x00\x00"

    async def send_frame(self, frame: bytes) -> None:
        self.sent_frames.append(frame)

    async def hangup(self) -> None:
        self.hangup_called = True

    async def transfer(self, destination: str) -> None:
        self.transfer_calls.append(destination)
        if self._transfer_raises:
            raise NotImplementedError("AudioSocketBridge.transfer(): sem ARI (fake de teste)")


class _FakeASR:
    def __init__(self, results: list[ASRResult]):
        self._results = results

    async def stream(self, audio_frames):
        async for _ in audio_frames:
            break  # dreno mínimo do iterador de entrada, como um ASR real faria
        for result in self._results:
            yield result


class _FakeTTS:
    def __init__(self, *, delay: float = 0.0):
        self.synthesized: list[str] = []
        self._delay = delay

    async def synthesize(self, text: str):
        self.synthesized.append(text)
        if self._delay:
            await asyncio.sleep(self._delay)
        yield f"audio:{text}".encode()


def _final(text: str, confidence: float = 0.95) -> ASRResult:
    return ASRResult(text=text, is_final=True, confidence=confidence)


def _partial(text: str) -> ASRResult:
    return ASRResult(text=text, is_final=False, confidence=0.3)


def _new_orchestrator() -> CallOrchestrator:
    return CallOrchestrator(connector=MockISPConnector(), llm=StubLLMClient())


async def test_full_call_identifies_and_completes_then_hangs_up():
    bridge = _FakeAudioBridge()
    asr = _FakeASR([_final("111.444.777-35"), _final("quero a segunda via do boleto")])
    tts = _FakeTTS()
    orch = _new_orchestrator()

    await run_call(bridge, asr, tts, orch)

    assert bridge.hangup_called is True
    assert orch.subscriber is not None
    assert orch.subscriber.id == "sub-001"
    # saudação + identificação + resposta do FIN-02 — pelo menos 3 falas sintetizadas
    assert len(tts.synthesized) >= 3


async def test_escalation_tries_transfer_then_hangs_up_even_when_not_implemented():
    bridge = _FakeAudioBridge(transfer_raises=True)
    asr = _FakeASR([_final("111.444.777-35"), _final("quero falar com atendente")])
    tts = _FakeTTS()
    orch = _new_orchestrator()

    await run_call(bridge, asr, tts, orch)

    assert bridge.transfer_calls  # tentou transferir
    assert bridge.hangup_called is True  # mesmo sem ARI, desliga em vez de travar a chamada


async def test_escalation_transfer_succeeds_when_implemented():
    bridge = _FakeAudioBridge(transfer_raises=False)
    asr = _FakeASR([_final("111.444.777-35"), _final("quero falar com atendente")])
    tts = _FakeTTS()
    orch = _new_orchestrator()

    await run_call(bridge, asr, tts, orch)

    assert bridge.transfer_calls == ["solicitação explícita de atendente"]
    assert bridge.hangup_called is True


async def test_ignores_empty_final_transcript():
    """ASR às vezes manda is_final=True com texto vazio (ruído/silêncio) —
    não pode virar um turno de conversa (viraria UNKNOWN/transbordo à toa)."""
    bridge = _FakeAudioBridge()
    asr = _FakeASR([_final(""), _final("111.444.777-35"), _final("quero a segunda via do boleto")])
    tts = _FakeTTS()
    orch = _new_orchestrator()

    await run_call(bridge, asr, tts, orch)

    assert orch.subscriber is not None  # identificação ainda funcionou normalmente


async def test_partial_result_interrupts_ongoing_speech():
    """Barge-in best-effort: um resultado parcial novo cancela a síntese
    em andamento (spec §5). `_listen` roda concorrente ao loop de fala —
    sem sincronizar explicitamente com um evento, o fake de ASR (sem
    nenhum I/O real) esvazia todos os resultados antes da saudação sequer
    começar a "falar" (achado rodando o teste sem essa sincronização: o
    `interrupt()` não tinha nenhuma task em andamento para cancelar)."""
    bridge = _FakeAudioBridge()
    speaking_started = asyncio.Event()

    class _SyncedSlowTTS:
        def __init__(self):
            self.synthesized: list[str] = []

        async def synthesize(self, text: str):
            self.synthesized.append(text)
            speaking_started.set()
            await asyncio.sleep(0.2)
            yield f"audio:{text}".encode()

    class _SyncedASR:
        async def stream(self, audio_frames):
            async for _ in audio_frames:
                break
            await speaking_started.wait()  # espera a saudação realmente começar a "falar"
            yield _partial("oi")
            yield _final("111.444.777-35")
            yield _final("quero a segunda via do boleto")

    tts = _SyncedSlowTTS()
    asr = _SyncedASR()
    orch = _new_orchestrator()

    await run_call(bridge, asr, tts, orch)

    # A síntese da saudação foi iniciada (texto registrado) mas cancelada
    # pelo "oi" parcial antes do `await asyncio.sleep` interno terminar —
    # nunca chegou a mandar o frame de áudio correspondente.
    assert len(bridge.sent_frames) < len(tts.synthesized)
    assert orch.subscriber is not None  # a chamada seguiu normalmente depois da interrupção
