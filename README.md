# VOX-ISP

Atendente virtual de voz para provedores regionais de internet (ISPs), complementando o call center humano para reduzir o número de atendentes necessários via **contenção** (resolução total sem humano) das intenções mais repetitivas — 2ª via de boleto, "estou sem internet", status de OS, desbloqueio de confiança — e **transbordo contextualizado** para as demais.

Especificação técnica completa: [`spec-voicebot-isp.md`](./spec-voicebot-isp.md).

> Este repositório contém o **scaffold inicial** do produto: a FSM de conversa, o modelo de dados, o "ISP Connector Hub" (interface abstrata para ERPs de provedor) e um orquestrador que já reproduz o fluxo de exemplo da spec (§7.2 — "estou sem internet" com correlação de massivo). ASR/TTS/telefonia real (Deepgram, ElevenLabs, Asterisk/LiveKit) entram como implementações plugáveis nas próximas fases — ver [Status e próximos passos](#status-e-próximos-passos).

## Por que isso existe

Provedores de 10k–200k assinantes concentram 60–75% das ligações em um punhado de intenções repetitivas. O objetivo é absorver as resolvíveis sem humano (meta: 55–65% de contenção em 12 meses) e entregar as demais ao atendente **já com contexto carregado**, reduzindo o AHT. Detalhes de negócio, compliance (Decreto 11.034/SAC, RGC/Anatel, LGPD) e ROI estão na spec.

## Arquitetura implementada nesta v1

```
Cliente (texto/API HTTP de dev)
        │
        ▼
CallOrchestrator  ── FSM (CallFSM) decide estado/transições e regras invioláveis
        │                 │
        │                 └─ intents destrutivos exigem confirmação verbal
        ▼
StubLLMClient (classificador por keyword — trocar por Claude Haiku/GPT-4o-mini/Gemini Flash)
        │
        ▼
ISPConnector (Protocol) ── MockISPConnector (dev/teste)
        │                   └─ implementações reais: Hubsoft, IXC, SGP, Voalle, MK (a construir)
        ▼
check_massive_incident()  ── correlação PON→LOS→incidente (spec §4.5, maior ROI do produto)
```

A árvore completa de arquitetura (borda telefônica, voice runtime, orçamento de latência) está em `spec-voicebot-isp.md` §3.

## Estrutura do repositório

```
src/voxisp/
  fsm/            máquina de estados + catálogo de intents + regras invioláveis (§7.1)
  connectors/     ISPConnector (Protocol), modelos Pydantic, MockISPConnector
  orchestrator/   CallOrchestrator (FSM + LLM + Connector Hub) e cliente LLM plugável
  massive_detection.py   algoritmo de correlação de massivo (§4.5)
  voice/          interfaces de ASR/TTS (stubs — plugar Deepgram/ElevenLabs/etc.)
  telephony/      contrato da ponte de áudio (stub — plugar Asterisk/LiveKit/Pipecat)
  db/schema.sql   modelo de dados núcleo (§8): call, turn, action, escalation, incident_link
  main.py         API HTTP de demonstração (não é o runtime de voz final)
tests/            FSM, conector mock, detecção de massivo, orquestrador ponta a ponta
```

## Rodando localmente

Requer Python 3.12+.

```bash
make install      # cria venv e instala em modo editável + deps de dev
make test         # roda a suíte de testes (23 testes, sem infra externa)
make up           # sobe Postgres + Redis via docker compose (opcional nesta fase)
make dev          # sobe a API de demonstração em http://localhost:8000
```

Exemplo de chamada simulada via HTTP (reproduz o fluxo do §7.2 da spec):

```bash
curl -s -X POST localhost:8000/calls                                    # inicia chamada, recebe call_id
curl -s -X POST localhost:8000/calls/<call_id>/identify \
     -H 'Content-Type: application/json' -d '{"cpf":"111.444.777-35"}'  # identificação
curl -s -X POST localhost:8000/calls/<call_id>/utterance \
     -H 'Content-Type: application/json' -d '{"text":"Minha internet parou."}'
```

Copie `.env.example` para `.env` para ajustar conector/providers.

## Status e próximos passos

Implementado (Fase 1 do roadmap da spec, §10):
- FSM com as 6 regras invioláveis do §7.1 (transbordo em 1 palavra, confirmação de ação destrutiva, limites de falha de reconhecimento e de turnos sem progresso)
- `ISPConnector` (interface) + `MockISPConnector` (dados de demonstração, inclui cenário de massivo)
- Identificação por CPF com validação de dígito verificador
- Intents FIN-02, FIN-03, NET-01→03 (fluxo completo do exemplo §7.2), OPS-01
- Payload de transbordo contextualizado (§7.3)
- Modelo de dados (§8) como SQL pronto para Postgres

Pendente — próximas fases:
1. Conectores reais de ERP (`HubsoftConnector`, `IXCSoftConnector`, ...) e de telemetria (RADIUS, ACS, OLT/SNMP, Zabbix)
2. LLM real com prompt caching (substituir `StubLLMClient`) e tool-calling com allowlist por estado
3. ASR/TTS de produção (Deepgram/Azure + ElevenLabs/Cartesia) e voice runtime (LiveKit Agents ou Pipecat) ligado a Asterisk/Kamailio
4. Persistência real (SQLAlchemy + Postgres) das tabelas `call`/`turn`/`action`/`escalation`
5. Parecer jurídico formal (Decreto 11.034, RGC, LGPD) antes de qualquer go-live — ver spec §6

Ver `spec-voicebot-isp.md` §10 (roadmap) e §14 (próximos passos) para o plano completo.
