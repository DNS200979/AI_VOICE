# Voice runtime (`voice/runtime.py`)

Liga `AudioBridge` + `ASREngine` + `CallOrchestrator` + `TTSEngine` numa
chamada real, turno a turno — a peça que a spec §3 chama de "VOICE RUNTIME
(LiveKit Agents ou Pipecat)".

## Decisão: nenhum framework externo adotado

LiveKit Agents e Pipecat são desenhados em torno de um LLM conduzindo a
conversa livremente dentro do pipeline deles — exatamente o padrão que a
spec §3.2 recusa explicitamente ("O LLM não dirige a conversa
livremente... a FSM decide"). Adotar qualquer um dos dois exigiria
reescrever o `CallOrchestrator` (FSM + tool-calling, já testado com 190+
casos ao longo do projeto) dentro da abstração de frames/pipeline deles,
trocando o controle explícito — o ponto central desta arquitetura — pela
conveniência de um framework pronto. `voice/runtime.py` é um loop próprio
e enxuto que só liga as 4 peças já construídas nesta sessão
(`AudioSocketBridge`, `DeepgramASR`, `CallOrchestrator`, `ElevenLabsTTS`).

## O que o loop faz

- **Escuta e fala concorrentes**: uma task dedicada (`_listen`) consome
  `asr.stream()` continuamente, independente do loop de conversa estar
  ocupado esperando uma síntese terminar. Sem essa concorrência,
  barge-in nunca teria chance de interromper nada — um loop sequencial só
  volta a olhar o ASR depois de terminar de falar (bug real encontrado
  escrevendo `tests/test_voice_runtime.py`; a primeira versão deste
  módulo era sequencial e tinha exatamente esse defeito).
- **Barge-in best-effort** (spec §5, corte ≤150ms): um resultado
  *parcial* novo do ASR cancela a síntese em andamento, via
  `asyncio.Task.cancel()`. Não usa `UtteranceEnd`/`SpeechStarted` da
  Deepgram (não consumidos por `DeepgramASR`, ver docs/voice/deepgram.md).
- **Dispatch identificação vs. turno livre**: olha `orchestrator.fsm.state`
  para decidir entre `orchestrator.identify(text)` (durante
  `CallState.IDENTIFICATION`) e `orchestrator.handle_utterance(text)` — o
  mesmo dispatch que `main.py` faz via duas rotas HTTP separadas.
- **Transbordo**: tenta `audio_bridge.transfer(...)`; como
  `AudioSocketBridge.transfer()` levanta `NotImplementedError` (confirmado
  ausente do protocolo AudioSocket, ver docs/telephony/audiosocket.md),
  loga o erro e desliga de qualquer forma — nunca trava a chamada, mas é
  uma falha operacional real (cliente que devia ir para humano não vai).

## O que nunca foi testado com todas as pontas reais simultâneas

`AudioSocketBridge` foi validado contra Asterisk real (Docker);
`DeepgramASR`/`ElevenLabsTTS` só contra documentação e fakes (sem conta
paga disponível). Este módulo em si só foi testado com as 4 peças
fake/stub, mais o `CallOrchestrator` real (`MockISPConnector` +
`StubLLMClient`) — nunca as 4 pontas reais ao mesmo tempo (Asterisk +
Deepgram + Claude + ElevenLabs). Falta ainda:

1. Formato de áudio de saída — `AudioSocketBridge` espera PCM16, não
   G.711 A-law (ver limitação em docs/telephony/audiosocket.md)
2. `transfer()` via ARI channel redirect
3. Medir a latência de turno real (spec §5.1: p50 ≤700ms) — só dá para
   medir com todas as pontas reais conectadas ao mesmo tempo
4. Decidir se o barge-in best-effort daqui é suficiente ou se precisa de
   VAD dedicado (Silero, como a spec sugere no diagrama do §3) antes do
   ASR entrar em cena
