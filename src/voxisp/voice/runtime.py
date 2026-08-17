"""Voice runtime — liga `AudioBridge` + `ASREngine` + `CallOrchestrator` +
`TTSEngine` numa chamada real, turno a turno. Spec §3.

**Decisão de arquitetura**: a spec cita "VOICE RUNTIME (LiveKit Agents ou
Pipecat)" como a peça que junta tudo isso. Nenhum framework externo é
adotado aqui — os dois (LiveKit Agents e Pipecat) são desenhados em torno
de um LLM conduzindo a conversa livremente dentro do pipeline deles, que é
exatamente o padrão que a spec §3.2 recusa ("O LLM não dirige a conversa
livremente... a FSM decide"). Adotar qualquer um dos dois exigiria
reescrever o `CallOrchestrator` (FSM + tool-calling já testado com 190+
casos) dentro da abstração deles, perdendo o controle explícito que é o
ponto central da arquitetura. Este módulo é um loop próprio e enxuto que
só liga as 4 peças já construídas nesta sessão — não implementa jitter
buffer nem resampling (spec §3 trata isso como responsabilidade da borda
telefônica, não do voice runtime).

**Nunca testado de ponta a ponta com todas as pontas reais simultâneas**
(Asterisk real + Deepgram real + ElevenLabs real): `AudioSocketBridge` foi
validado contra Asterisk real (Docker); `DeepgramASR`/`ElevenLabsTTS` só
contra documentação e fakes (sem conta paga disponível, ver
docs/voice/deepgram.md e docs/voice/elevenlabs.md); este módulo em si é
testado com as 4 peças fake/stub (`tests/test_voice_runtime.py`). Ver
docs/telephony/audiosocket.md para o que falta para uma validação real
completa.
"""
from __future__ import annotations

import asyncio
import contextlib

from voxisp.fsm.states import CallState
from voxisp.observability.logging import get_logger
from voxisp.orchestrator.turn_manager import CallOrchestrator, TurnResult
from voxisp.telephony.audio_bridge import AudioBridge
from voxisp.voice.asr import ASREngine
from voxisp.voice.tts import TTSEngine

logger = get_logger(__name__)


async def run_call(
    audio_bridge: AudioBridge,
    asr: ASREngine,
    tts: TTSEngine,
    orchestrator: CallOrchestrator,
) -> None:
    """Roda uma chamada inteira: saudação, identificação, turnos livres,
    até transbordo ou encerramento. Bloqueia até a chamada acabar.

    Barge-in (spec §5, corte ≤150ms): best-effort — cancela a síntese em
    andamento assim que um resultado *parcial* novo chega do ASR, sem
    esperar `is_final`. Isso é o mais perto que dá de "interrupção" sem um
    detector de VAD dedicado no runtime (a Deepgram já faz VAD do lado
    dela, mas `UtteranceEnd`/`SpeechStarted` não são consumidos aqui — ver
    docs/voice/deepgram.md, limitações).

    Escutar (`asr.stream`) e falar (`speech.say`/`wait_until_done`) rodam
    em **duas tasks concorrentes** de propósito — `_listen` numa task à
    parte, o loop de conversa aqui. Sem isso, barge-in nunca teria chance
    de interromper nada: um loop sequencial só volta a olhar o ASR depois
    de terminar de falar (achado escrevendo os testes deste módulo — a
    primeira versão sequencial tinha exatamente esse bug).
    """
    speech = _SpeechController(audio_bridge, tts)
    transcripts: asyncio.Queue[str] = asyncio.Queue()
    listener = asyncio.ensure_future(_listen(audio_bridge, asr, speech, transcripts))

    try:
        greeting = await orchestrator.greet()
        await speech.say(greeting.text)
        await speech.wait_until_done()

        while True:
            text = await transcripts.get()
            turn = await _dispatch_turn(orchestrator, text)
            await speech.say(turn.text)
            await speech.wait_until_done()

            if turn.escalate:
                await _try_transfer(audio_bridge, turn)
                await audio_bridge.hangup()
                return
            if orchestrator.fsm.state == CallState.CLOSING:
                await orchestrator.finish()
                await audio_bridge.hangup()
                return
    finally:
        listener.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await listener


async def _listen(
    audio_bridge: AudioBridge,
    asr: ASREngine,
    speech: _SpeechController,
    transcripts: asyncio.Queue[str],
) -> None:
    """Task independente: nunca para de consumir `asr.stream()`, mesmo
    enquanto o loop de conversa está ocupado falando/esperando — é essa
    concorrência que permite um resultado parcial interromper uma fala em
    andamento em vez de só ser visto depois que ela termina."""
    async for result in asr.stream(audio_bridge.receive_frames()):
        if not result.is_final:
            speech.interrupt()
            continue
        if result.text.strip():
            await transcripts.put(result.text)


async def _dispatch_turn(orchestrator: CallOrchestrator, text: str) -> TurnResult:
    """A FSM só expõe um método dedicado (`identify`) para o turno de
    identificação por CPF — os demais passam por `handle_utterance`. O
    voice runtime decide qual chamar olhando o estado atual, o mesmo
    dispatch que `main.py` faz via duas rotas HTTP separadas
    (`/identify` vs `/utterance`)."""
    if orchestrator.fsm.state == CallState.IDENTIFICATION:
        return await orchestrator.identify(text)
    return await orchestrator.handle_utterance(text)


async def _try_transfer(audio_bridge: AudioBridge, turn: TurnResult) -> None:
    reason = turn.escalation.reason if turn.escalation else "transbordo"
    try:
        await audio_bridge.transfer(reason)
    except NotImplementedError:
        # AudioSocketBridge.transfer() confirmadamente não existe sem ARI
        # channel redirect (ver telephony/audio_bridge.py) — loga e ainda
        # assim desliga em vez de travar a chamada, mas isso É uma falha
        # operacional real: o cliente devia ter ido para um humano.
        logger.error("transfer_not_implemented", reason=reason)


class _SpeechController:
    """Gerencia a task de síntese/envio de áudio em andamento — permite
    cancelar no meio (barge-in) e esperar terminar (antes de desligar)."""

    def __init__(self, audio_bridge: AudioBridge, tts: TTSEngine) -> None:
        self._audio_bridge = audio_bridge
        self._tts = tts
        self._task: asyncio.Task | None = None

    async def say(self, text: str) -> None:
        self.interrupt()
        self._task = asyncio.ensure_future(self._speak(text))

    def interrupt(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def wait_until_done(self) -> None:
        if self._task is None:
            return
        with contextlib.suppress(asyncio.CancelledError):
            await self._task

    async def _speak(self, text: str) -> None:
        async for chunk in self._tts.synthesize(text):
            await self._audio_bridge.send_frame(chunk)
