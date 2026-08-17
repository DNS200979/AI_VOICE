# AudioSocketBridge / AudioSocketServer

`src/voxisp/telephony/audio_bridge.py` implementa `AudioBridge` de
verdade contra o protocolo **AudioSocket** do Asterisk (`app_audiosocket`
no dialplan / `res_audiosocket` por baixo) — um dos dois caminhos que a
spec cita (§3: "AudioSocket ou ARI externalMedia"). ARI externalMedia
**não** foi implementado (fica como alternativa não explorada).

Diferente de Deepgram/ElevenLabs: **Asterisk é open-source e
self-hospedável** — este adapter foi validado de ponta a ponta contra um
Asterisk real (Docker), não só contra documentação. Ver "Validação real"
abaixo.

## Protocolo (verificado)

Fonte: https://docs.asterisk.org/Configuration/Channel-Drivers/AudioSocket/

Cabeçalho de 3 bytes por mensagem: 1 byte de tipo + 2 bytes de tamanho do
payload (`uint16` **big-endian**).

| Tipo | Valor | Payload | Uso aqui |
|---|---|---|---|
| Terminate | `0x00` | vazio | Encerra a chamada — `receive_frames()` para de iterar; `hangup()` manda este tipo antes de fechar o socket |
| UUID | `0x01` | 16 bytes binários | Identificador da chamada, formatado como UUID textual em `bridge.call_uuid` |
| DTMF | `0x03` | 1 byte ASCII | Acumulado em `bridge.dtmf_digits` — spec §4.1: fallback de DTMF para CPF após falha de reconhecimento de voz |
| Áudio 8kHz | `0x10` | PCM signed-linear 16-bit **little-endian** mono | O único tipo de áudio que `receive_frames()` repassa — a stack inteira assume 8kHz (spec §3) |
| Áudio 12/16/48kHz | `0x11`/`0x12`/`0x16` | idem, outra taxa | Ignorados de propósito |
| Erro | `0xff` | texto opcional | Levanta `AudioSocketError` |

`send_frame()` sempre manda tipo `0x10` (áudio 8kHz) — o voice runtime
(`voice/runtime.py`) só produz áudio nessa taxa (via `TTSEngine`
configurado para `output_format=alaw_8000`/`pcm_8000`... **atenção**: o
payload aqui é PCM signed-linear 16-bit, não G.711 A-law — se o
`TTSEngine` real estiver configurado para `alaw_8000`, precisa decodificar
A-law → PCM16 antes de mandar por `send_frame()`, ou reconfigurar o TTS
para `pcm_8000` diretamente. **Não implementado** — ver Limitações.

## Dialplan (Asterisk)

```
exten => 1000,1,Answer()
 same => n,AudioSocket(<uuid-da-chamada>,<host-do-runtime>:<porta>)
 same => n,Hangup()
```

`AudioSocketServer` sobe um `asyncio.start_server` TCP simples — uma
conexão por chamada, cada uma vira um `AudioSocketBridge` entregue ao
callback `on_connect` (tipicamente `voice.runtime.run_call`).

## Validação real

Rodado contra `andrius/asterisk:20` via Docker (`--network host`), com um
dialplan mínimo: uma extensão `AudioSocket(...)` e um canal `Local`
gerando tom real via `Milliwatt()` (aplicação nativa do Asterisk, sem
precisar de arquivo de som) bridgeado com ela, chamada originada via
`asterisk -rx "channel originate Local/1000@testctx extension 2000@audiosource"`
(sem precisar de softphone/trunk SIP).

Resultado real capturado por `AudioSocketBridge.receive_frames()`:

```
call_uuid: '11111111-1111-1111-1111-111111111111'  # bate exatamente com o do dialplan
frame_count: 5
frame_sizes: [320, 320, 320, 320, 320]
nonzero_bytes: 1597 de 1600  # tom real do Milliwatt(), não silêncio
```

**Achado real, não documentado explicitamente na referência do
protocolo**: cada frame de áudio do Asterisk tem exatamente **320 bytes**
= 160 amostras × 16 bits = **20ms de PCM a 8kHz** — o tamanho de frame
padrão interno do Asterisk (`ast_format_slin`, 20ms). O voice runtime não
precisa (nem deve) tentar rebufferizar em outro tamanho de frame — 20ms é
o que chega e o que deve ser mandado de volta.

`hangup()` (chamado depois de capturar os frames no teste) encerrou a
chamada sem warning/erro nos logs do Asterisk.

## Limitações conhecidas

- **`transfer()` não implementado — confirmado, não é omissão.** O
  protocolo AudioSocket não tem uma mensagem de "transfira esta chamada".
  Transbordo real (spec §7.3) precisa de **ARI channel redirect**, uma
  API HTTP/WebSocket totalmente separada do socket TCP do AudioSocket,
  operando sobre o canal Asterisk associado ao `call_uuid`. Fica como
  próximo passo — precisa de acesso ARI (`ari.conf`, usuário/senha) além
  do AudioSocket.
- **Payload de áudio é PCM16, não G.711 A-law.** `ElevenLabsTTS` default
  (`docs/voice/elevenlabs.md`) usa `output_format=alaw_8000` — enviar
  esses bytes direto via `send_frame()` mandaria A-law rotulado como
  PCM16 para o Asterisk, tocando errado. Duas opções não implementadas
  aqui: (a) configurar `TTS_OUTPUT_FORMAT=pcm_8000` no `.env` (evita
  qualquer conversão, mas perde a vantagem de "mesmo codec da telefonia");
  (b) decodificar A-law → PCM16 antes de `send_frame()` (uma tabela de
  256 entradas, барato de implementar, mas não feito nesta sessão).
- **DTMF capturado mas não consumido.** `bridge.dtmf_digits` acumula os
  dígitos recebidos — o voice runtime (`voice/runtime.py`) não lê isso
  ainda para o fallback de CPF do §4.1.
- **Sem jitter buffer/resampling** — spec §3 trata isso como
  responsabilidade da borda telefônica (Asterisk já entrega frames
  regulares de 20ms), não do runtime.
- **Validado com tom sintético (`Milliwatt`), não voz humana real nem
  trânsito por SIP trunk de verdade** — confirma o protocolo/framing,
  não a qualidade/latência em produção.

## Quando houver acesso a um ambiente real

1. Decidir o formato de áudio de saída (PCM16 direto vs decodificar
   A-law) e implementar a conversão que faltar
2. Implementar `transfer()` via ARI channel redirect
3. Consumir `dtmf_digits` no voice runtime para o fallback de CPF (§4.1)
4. Testar com um trunk SIP real (ou pelo menos um softphone) em vez de
   `Local`/`Milliwatt()` — validar latência ponta a ponta contra o
   orçamento do §5.1
5. Kamailio (SBC) na frente do Asterisk — não testado nesta sessão, só o
   Asterisk isolado
