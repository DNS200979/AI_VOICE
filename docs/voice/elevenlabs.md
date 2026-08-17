# ElevenLabsTTS

`src/voxisp/voice/tts.py` fala com a API de streaming de TTS da
[ElevenLabs](https://elevenlabs.io/) (modelo Flash) — o candidato de TTS
que a spec lista primeiro (§4.2).

Endpoint e formato verificados em:
- https://elevenlabs.io/docs/api-reference/text-to-speech/stream

**Mesma ressalva do `docs/voice/deepgram.md`: não há como testar isto
contra o servidor real sem uma conta paga da ElevenLabs.** Validado só
contra a documentação pública e testes com `httpx.MockTransport`
(`tests/test_elevenlabs_tts.py`) — não contra síntese real. Validar com
uma conta de teste (e ouvir o áudio de verdade) antes de qualquer piloto.

## Autenticação

Header `xi-api-key: <api_key>` — não é `Authorization: Bearer`, é um
header próprio da ElevenLabs.

## Endpoint

```
POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream?output_format=alaw_8000
```

| Campo | Valor usado | Por quê |
|---|---|---|
| `voice_id` | `TTS_VOICE_ID` (obrigatório, sem default) | Voz específica da conta ElevenLabs do provedor — **não existe um valor genérico possível**, precisa ser escolhida/clonada na conta real |
| `model_id` | `eleven_flash_v2_5` | O modelo "Flash" citado na spec — otimizado para menor time-to-first-byte (alvo ≤200ms, spec §4.2), não o `eleven_multilingual_v2` (default da API se omitido, mais lento) |
| `language_code` | `pt` | ISO 639-1 |
| `output_format` (query) | `alaw_8000` | G.711 A-law 8kHz — mesmo codec de telefonia do Brasil (spec §3: "Codec G.711a / Opus"), evita resample extra na borda telefônica. A API também aceita `ulaw_8000` (G.711 µ-law, padrão América do Norte/Japão) e variantes PCM (`pcm_8000`...`pcm_48000`)/MP3/Opus/WAV — configurável via `TTS_OUTPUT_FORMAT` se a ponte de áudio real usar outro codec |

## Resposta

Áudio bruto em streaming via chunked transfer encoding — sem
framing/protocolo próprio, só os bytes crus do codec pedido em
`output_format`. `synthesize()` repassa cada chunk assim que chega
(`response.aiter_bytes()`), sem bufferizar a resposta inteira antes de
render — atende ao requisito de streaming obrigatório do §4.2.

## Limitações conhecidas

- **SSML não testado.** Spec §4.2 pede SSML para "valores em reais,
  datas, linha digitável em grupos, pausas" — a ElevenLabs suporta tags
  de pausa/ênfase em texto (não SSML XML completo como Azure), mas isso
  não foi validado aqui; quem monta o texto final (`draft_turn` no
  orquestrador) precisa saber o que a ElevenLabs realmente aceita antes
  de confiar nisso para valores financeiros.
- **Barge-in (cancelamento) não implementado neste adapter.** Spec §4.2
  exige "cancelamento imediato do buffer no barge-in" — isso é
  responsabilidade de quem consome o `AsyncIterator[bytes]` (o voice
  runtime real): parar de iterar e fechar a conexão HTTP cancela o
  request no lado do cliente, mas o adapter em si não expõe um método
  `cancel()` dedicado.
- **`voice_settings` (stability/similarity_boost/style/speed) usa os
  defaults da API** — não configurável via `.env` nesta v1. Pode
  precisar de ajuste fino por provedor (voz de marca, spec §4.2) quando
  houver piloto.

## Quando houver acesso a uma conta real

1. Preencher `TTS_PROVIDER=elevenlabs`, `TTS_API_KEY` e `TTS_VOICE_ID`
   (escolher/clonar a voz na conta real primeiro) no `.env`
2. Ouvir o áudio de verdade — confirmar que `alaw_8000` toca certo na
   ponte de telefonia real, sem estalos/velocidade errada
3. Medir o time-to-first-byte real contra o alvo de ≤200ms (spec §4.2)
4. Validar como a voz pronuncia valores em reais/datas/linha digitável —
   decidir se dá para confiar no texto puro ou se precisa de alguma
   marcação especial da ElevenLabs
