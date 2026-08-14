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
  connectors/     ISPConnector (Protocol), modelos Pydantic, MockISPConnector,
                  HubsoftConnector (stub — ver docs/connectors/hubsoft.md),
                  resilience.py (timeout + retry + circuit breaker, §4.4)
  orchestrator/   CallOrchestrator (FSM + LLM + Connector Hub); StubLLMClient (dev) e
                  ClaudeLLMClient (Haiku 4.5, saída estruturada) plugáveis via get_llm_client();
                  tool_allowlist.py + tools.py + tool_executor.py — tool-calling real do Claude
                  restrito ao allowlist de tools por estado da FSM (§3.2/§4.3)
  massive_detection.py   algoritmo de correlação de massivo (§4.5)
  voice/          interfaces de ASR/TTS (stubs — plugar Deepgram/ElevenLabs/etc.)
  telephony/      contrato da ponte de áudio (stub — plugar Asterisk/LiveKit/Pipecat)
  db/schema.sql   modelo de dados núcleo (§8): call, turn, action, escalation, incident_link
  db/models.py    mesmos modelos como ORM SQLAlchemy 2.0 async (Postgres em prod, SQLite em teste)
  db/repository.py  CallRepository — grava call/turn/action/escalation nos pontos de gravação
                  do CallOrchestrator (opcional: PERSISTENCE_ENABLED=false por padrão)
  main.py         API HTTP de demonstração (não é o runtime de voz final)
tests/            FSM, conector mock, detecção de massivo, orquestrador ponta a ponta
```

## Rodando localmente

Requer Python 3.12+.

```bash
make install      # cria venv e instala em modo editável + deps de dev
make test         # roda a suíte de testes (64 testes, sem infra externa — inclui SQLite in-memory)
make up           # sobe Postgres + Redis via docker compose (só necessário com PERSISTENCE_ENABLED=true)
make dev          # sobe a API de demonstração em http://localhost:8000
```

Por padrão a API roda **100% em memória** (nada é gravado). Para persistir call/turn/action/escalation
de verdade: `make up` (sobe Postgres) e `PERSISTENCE_ENABLED=true` no `.env` — as tabelas são criadas
automaticamente no boot (atalho de dev; em produção prefira aplicar `db/schema.sql` ou uma migração real).

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
- Camada de resiliência (`resilience.py`): timeout, retry com backoff, circuit breaker (§4.4)
- `HubsoftConnector` **stubado**, plugado na fábrica (`ISP_CONNECTOR=hubsoft`), esperando a
  documentação da API — checklist completo em [`docs/connectors/hubsoft.md`](./docs/connectors/hubsoft.md)
- `ClaudeLLMClient` — classificador de intenção real via Claude (Haiku 4.5 por padrão, saída
  estruturada validada por schema, timeout duro de 1,5s com degradação para transbordo). Ativar com
  `LLM_PROVIDER=anthropic` + `LLM_API_KEY` no `.env`; `StubLLMClient` (keyword, sem rede) continua
  como padrão em dev/CI
- **Persistência real** (SQLAlchemy 2.0 async): `Call`/`Turn`/`Action`/`Escalation`/`IncidentLink`
  gravados nos pontos de gravação do `CallOrchestrator` — turno a turno, ação com `idempotency_key`
  (spec §5), escalonamento com payload de contexto (§7.3), fechamento com `duration_s`/`outcome`.
  Opcional (`PERSISTENCE_ENABLED=false` por padrão); mesmo código roda contra Postgres em produção
  ou SQLite in-memory nos testes
- **Tool-calling real com allowlist por estado** (§3.2/§4.3): `ToolExecutor` roda um loop de
  tool-use de verdade contra o Claude, mas só declara as tools que `tool_allowlist.py` autoriza
  para o estado atual da FSM — nunca em SLOT_COLLECTION/CONFIRMATION, só em EXECUTION, e para
  tools destrutivas isso só é alcançável depois da confirmação verbal (regra §7.1 #2), por
  construção da FSM. Duas camadas de defesa: só a allowlist é declarada na request, e qualquer
  `tool_use` fora dela é recusado sem executar o conector. Já ligado ao fluxo NET-01 (diagnóstico);
  opcional (`LLM_PROVIDER=anthropic`), com fallback determinístico se o modelo pular uma tool
  obrigatória

Pendente — próximas fases:
1. Preencher o `HubsoftConnector` assim que a documentação/credenciais da API chegarem (ou escrever `IXCSoftConnector`/`VoalleConnector` se o piloto for outro ERP), além dos adapters de telemetria (RADIUS, ACS, OLT/SNMP, Zabbix)
2. Estender o tool-calling real (hoje só em NET-01) para FIN-02/FIN-03/OPS-01, com os handlers chamando `fsm.request_action()` antes de gatilhar o `ToolExecutor`
3. ASR/TTS de produção (Deepgram/Azure + ElevenLabs/Cartesia) e voice runtime (LiveKit Agents ou Pipecat) ligado a Asterisk/Kamailio
4. Migração real (Alembic) em vez do `create_all` de conveniência usado hoje no boot da API
5. Parecer jurídico formal (Decreto 11.034, RGC, LGPD) antes de qualquer go-live — ver spec §6

Ver `spec-voicebot-isp.md` §10 (roadmap) e §14 (próximos passos) para o plano completo.
