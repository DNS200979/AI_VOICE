# DeepgramASR

`src/voxisp/voice/asr.py` fala com o WebSocket de streaming da
[Deepgram](https://deepgram.com/) (Nova-3) — o candidato de ASR que a
spec lista primeiro ("melhor custo/latência", §4.1).

Endpoint, parâmetros e formato de mensagens verificados em:
- https://developers.deepgram.com/reference/listen-live (API reference —
  não o guia de exemplos via SDK oficial, que abstrai demais para
  reimplementar contra o protocolo bruto)
- https://developers.deepgram.com/reference/authentication

**Importante, diferente dos outros adapters deste projeto (Hubsoft,
GenieACS, Zabbix): não há como testar isto contra o servidor real sem uma
conta paga da Deepgram.** GenieACS e Zabbix são open-source e
self-hospedáveis — deu pra subir os dois via Docker e validar de ponta a
ponta nesta mesma sessão. A Deepgram é um serviço cloud fechado; o que
existe aqui foi validado só contra a documentação pública e uma bateria de
testes com WebSocket fake (`tests/test_deepgram_asr.py`) — **não** contra
tráfego real. Validar com uma conta de teste antes de qualquer piloto.

## Autenticação

Header `Authorization: Token <api_key>` — **não** é query param, apesar
de vários exemplos da doc (via SDK) esconderem isso. Nunca passar a API
key na URL (vazaria em logs de acesso do proxy/CDN).

## Conexão

```
wss://api.deepgram.com/v1/listen?model=nova-3&language=pt-BR&encoding=linear16&sample_rate=8000&channels=1&interim_results=true&punctuate=true&smart_format=true&endpointing=300
```

| Parâmetro | Valor usado | Por quê |
|---|---|---|
| `model` | `nova-3` | Único com suporte dedicado a `pt-BR` — `nova-2` tem multilíngue limitado a inglês↔espanhol (confirmado via changelog da Deepgram) |
| `language` | `pt-BR` | — |
| `encoding`/`sample_rate` | `linear16`/`8000` | PCM 16-bit 8kHz — assume que a ponte de telefonia (Asterisk) decodifica G.711 para PCM antes de repassar ao ASR. Configurável (`ASR_ENCODING`/`ASR_SAMPLE_RATE`) se a ponte real entregar outro formato |
| `interim_results` | `true` | Necessário para resultados parciais em streaming (spec §4.1: "resultados parciais") |
| `punctuate`/`smart_format` | `true` | `smart_format` ajuda no ITN (números por extenso → dígitos) exigido pelo §4.1 — não é validado contra CPF especificamente, isso continua em `_validate_cpf` no orquestrador |
| `endpointing` | `300` (ms) | Alvo de latência de finalização ≤300ms do §4.1 |

## Mensagens (verificadas)

**Recebidas** — só `type: "Results"` é consumido; `Metadata`,
`UtteranceEnd`, `SpeechStarted` são ignorados por enquanto (ver
Limitações):

```json
{
  "type": "Results",
  "is_final": true,
  "channel": {"alternatives": [{"transcript": "...", "confidence": 0.95}]}
}
```

**Enviadas**: frames de áudio como payload binário bruto; ao final do
stream, `{"type": "CloseStream"}` (texto) — dá tempo da Deepgram devolver
o resultado final pendente em vez de só derrubar o socket.

## Limitações conhecidas

- **`KeepAlive` não implementado.** A Deepgram fecha a conexão depois de
  ~12s sem receber áudio. `DeepgramASR.stream()` assume que
  `audio_frames` nunca para de produzir frames enquanto a chamada estiver
  ativa (o caso real de telefonia — silêncio ainda gera frames PCM de
  silêncio, não um gap). Se o voice runtime real puder pausar o envio de
  frames por mais de ~10s (ex.: hold), precisa mandar
  `{"type": "KeepAlive"}` periodicamente — não implementado.
- **`UtteranceEnd`/`SpeechStarted` ignorados.** Úteis para VAD/barge-in
  mais preciso (spec §5: barge-in ≤150ms) — o voice runtime real
  provavelmente quer consumir esses eventos também; hoje `stream()` só
  repassa `ASRResult`, perderia esse sinal.
- **Boosting de vocabulário não configurado** (`keywords`/`replace`) —
  spec §4.1 pede boosting para "ONU, ONT, roteador, PPPoE, boleto...",
  nomes de bairros/ruas da área de cobertura do provedor. Fica para
  quando houver um provedor piloto definindo esse vocabulário real.
- **Sem fallback DTMF.** Spec §4.1 pede fallback para DTMF após 1 falha
  de reconhecimento de CPF — isso é responsabilidade da ponte de
  telefonia/voice runtime (fora do escopo deste adapter de ASR).

## Quando houver acesso a uma conta real

1. Preencher `ASR_PROVIDER=deepgram` e `ASR_API_KEY` no `.env`
2. Validar contra áudio real 8kHz do provedor — WER alvo ≤12% (spec
   §4.1) precisa ser medido, não é garantido só pela configuração
3. Decidir se `ASR_ENCODING`/`ASR_SAMPLE_RATE` batem com o que a ponte de
   telefonia real (Asterisk, AudioSocket/ARI externalMedia) de fato entrega
4. Implementar `KeepAlive` se o voice runtime permitir pausas longas sem
   áudio (hold, transferência)
