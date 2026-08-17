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
ISPConnector (Protocol) ── MockISPConnector (dev/teste) · HubsoftConnector (real)
        │                   └─ IXC, SGP, Voalle, MK: ainda por construir
        ▼
check_massive_incident()  ── correlação PON→LOS→incidente (spec §4.5, maior ROI do produto)
```

A árvore completa de arquitetura (borda telefônica, voice runtime, orçamento de latência) está em `spec-voicebot-isp.md` §3.

## Estrutura do repositório

```
src/voxisp/
  fsm/            máquina de estados + catálogo de intents + regras invioláveis (§7.1)
  connectors/     ISPConnector (Protocol), modelos Pydantic, MockISPConnector,
                  HubsoftConnector (real, contra API pública documentada —
                  ver docs/connectors/hubsoft.md), resilience.py (timeout +
                  retry + circuit breaker, §4.4)
  orchestrator/   CallOrchestrator (FSM + LLM + Connector Hub); StubLLMClient (dev) e
                  ClaudeLLMClient (Haiku 4.5, saída estruturada) plugáveis via get_llm_client();
                  tool_allowlist.py + tools.py + tool_executor.py — tool-calling real do Claude
                  restrito ao allowlist de tools por estado da FSM (§3.2/§4.3); pt_datetime.py —
                  parser determinístico de data/hora em português (slot-filling de OPS-02 sem LLM)
  massive_detection.py   algoritmo de correlação de massivo (§4.5)
  voice/          ASREngine/TTSEngine (Protocol) + StubASR/StubTTS (dev); DeepgramASR (real,
                  WebSocket streaming, ver docs/voice/deepgram.md) e ElevenLabsTTS (real,
                  streaming HTTP, ver docs/voice/elevenlabs.md) plugáveis via
                  get_asr_engine()/get_tts_engine(); runtime.py — voice runtime próprio (não
                  LiveKit Agents/Pipecat, ver docs/voice/runtime.md) ligando AudioBridge+
                  ASR+CallOrchestrator+TTS com barge-in best-effort
  telephony/      AudioBridge (Protocol) + AudioSocketBridge/AudioSocketServer (real, contra
                  o protocolo AudioSocket do Asterisk, testado contra Asterisk real via
                  Docker — ver docs/telephony/audiosocket.md). ARI externalMedia e
                  Kamailio (SBC) não implementados
  db/schema.sql   modelo de dados núcleo (§8): call, turn, action, escalation, incident_link
  db/models.py    mesmos modelos como ORM SQLAlchemy 2.0 async (Postgres em prod, SQLite em teste)
  db/repository.py  CallRepository — grava call/turn/action/escalation nos pontos de gravação
                  do CallOrchestrator (opcional: PERSISTENCE_ENABLED=false por padrão)
  db/migrations/  migrações reais (Alembic, template async) geradas a partir de db/models.py —
                  ver "Persistência e migrações" abaixo
  main.py         API HTTP de demonstração (não é o runtime de voz final)
tests/            FSM, conector mock, detecção de massivo, orquestrador ponta a ponta
```

## Rodando localmente

Requer Python 3.12+.

```bash
make install      # cria venv e instala em modo editável + deps de dev
make test         # roda a suíte de testes (203 testes, sem infra externa — inclui SQLite in-memory)
make up           # sobe Postgres + Redis via docker compose (só necessário com PERSISTENCE_ENABLED=true)
make dev          # sobe a API de demonstração em http://localhost:8000
```

Por padrão a API roda **100% em memória** (nada é gravado). Para persistir call/turn/action/escalation
de verdade: `make up` (sobe Postgres) e `PERSISTENCE_ENABLED=true` no `.env`.

### Persistência e migrações

O schema real (Postgres) é gerenciado por **Alembic** (`db/migrations/`, template async) — o boot da
API **nunca** cria/altera tabelas sozinho (perigoso com múltiplas réplicas subindo ao mesmo tempo):
com `PERSISTENCE_ENABLED=true`, ele só confirma que a migração mais recente já foi aplicada e recusa
subir, com mensagem clara, se não tiver sido (`voxisp.db.session.check_migrations_applied`).

```bash
make up        # sobe o Postgres (docker compose)
make migrate   # aplica as migrações pendentes (alembic upgrade head)
make dev       # com PERSISTENCE_ENABLED=true no .env
```

Depois de mudar `db/models.py`, gere a revisão via autogenerate e **revise o arquivo antes de
commitar** (autogenerate não detecta tudo — renomes de coluna, algumas mudanças de tipo):

```bash
make migration m="descrição da mudança"
```

`create_all`/`init_models` (`db/session.py`) continuam existindo só para os testes automatizados
(SQLite in-memory, rápido e descartável) — nunca use isso contra Postgres real.

Exemplo de chamada simulada via HTTP (reproduz o fluxo do §7.2 da spec):

```bash
curl -s -X POST localhost:8000/calls                                    # inicia chamada, recebe call_id
curl -s -X POST localhost:8000/calls/<call_id>/identify \
     -H 'Content-Type: application/json' -d '{"cpf":"111.444.777-35"}'  # identificação
curl -s -X POST localhost:8000/calls/<call_id>/utterance \
     -H 'Content-Type: application/json' -d '{"text":"Minha internet parou."}'
```

Copie `.env.example` para `.env` para ajustar conector/providers.

Em vez de montar `curl` na mão, `scripts/talk.py` dá um REPL de terminal
que fala com essa mesma API turno a turno (com `make dev` rodando):

```bash
.venv/bin/python scripts/talk.py
```

## Testes prévios (antes de um ISP piloto)

- `make test` já cobre os 3 conectores (Mock/Hubsoft/adapters) e o
  orquestrador inteiro contra `httpx.MockTransport`/fakes — sem rede, mas
  também sem validar contra software real.
- `docker-compose.test-infra.yml` sobe GenieACS e Zabbix reais localmente
  (com um CPE TR-069 simulado) para validar `GenieACSAdapter`/
  `ZabbixAdapter` de ponta a ponta sem depender de um provedor piloto —
  passo a passo em
  [`docs/testing/local-integration-stack.md`](./docs/testing/local-integration-stack.md),
  smoke test em `scripts/smoke_test_acs_nms.py`.
- A Hubsoft **não tem sandbox público** — confirmado, credenciais de API só
  são liberadas para clientes pagantes (ver `docs/connectors/hubsoft.md`).
  Não há como testar o `HubsoftConnector` contra a API real sem ser um
  cliente/piloto.

## Status e próximos passos

Implementado (Fase 1 do roadmap da spec, §10):
- FSM com as 6 regras invioláveis do §7.1 (transbordo em 1 palavra, confirmação de ação destrutiva, limites de falha de reconhecimento e de turnos sem progresso)
- `ISPConnector` (interface) + `MockISPConnector` (dados de demonstração, inclui cenário de massivo) + `HubsoftConnector` (real)
- Identificação por CPF com validação de dígito verificador
- Intents FIN-02, FIN-03, NET-01→04, OPS-01→03 (fluxo completo do exemplo NET-01 em §7.2)
- Payload de transbordo contextualizado (§7.3)
- Modelo de dados (§8) como SQL pronto para Postgres
- Camada de resiliência (`resilience.py`): timeout, retry com backoff, circuit breaker (§4.4)
- **`HubsoftConnector` real** (`ISP_CONNECTOR=hubsoft`): fala com a API pública da Hubsoft — OAuth2
  password grant, `find_subscriber`/`get_invoices`/`issue_second_copy`/`request_trust_unlock`/
  `get_connection_status`/`list_service_orders`/`create_service_order`/`create_protocol` contra
  endpoints reais verificados na documentação oficial. `olt_id` é resolvido correlacionando a PON
  do assinante com `GET /rede/equipamento` (best-effort, nunca quebra `find_subscriber`); `cto_id`/
  `cpe_serial` confirmadamente **não existem** em nenhum dos 3 endpoints de rede da Hubsoft — assim
  como `get_cpe_diagnostics`/`reboot_cpe`/`get_area_incidents`, que continuam `NotImplementedError`
  (vêm de ACS/NMS, não do ERP). Mapeamento completo e limitações conhecidas em
  [`docs/connectors/hubsoft.md`](./docs/connectors/hubsoft.md)
- `ClaudeLLMClient` — classificador de intenção real via Claude (Haiku 4.5 por padrão, saída
  estruturada validada por schema, timeout duro de 1,5s com degradação para transbordo). Ativar com
  `LLM_PROVIDER=anthropic` + `LLM_API_KEY` no `.env`; `StubLLMClient` (keyword, sem rede) continua
  como padrão em dev/CI
- **Persistência real** (SQLAlchemy 2.0 async): `Call`/`Turn`/`Action`/`Escalation`/`IncidentLink`
  gravados nos pontos de gravação do `CallOrchestrator` — turno a turno, ação com `idempotency_key`
  (spec §5), escalonamento com payload de contexto (§7.3), fechamento com `duration_s`/`outcome`.
  Opcional (`PERSISTENCE_ENABLED=false` por padrão); mesmo código roda contra Postgres em produção
  ou SQLite in-memory nos testes
- **Tool-calling real com allowlist por estado** (§3.2/§4.3), em **todos os 7 intents
  implementados** (NET-01, NET-04, FIN-02, FIN-03, OPS-01, OPS-02, OPS-03): `ToolExecutor` roda um
  loop de tool-use de verdade contra o Claude, mas só declara as tools que `tool_allowlist.py`
  autoriza para o estado atual da FSM — nunca em SLOT_COLLECTION/CONFIRMATION, só em EXECUTION.
  Duas camadas de defesa: só a allowlist é declarada na request, e qualquer `tool_use` fora dela é
  recusado sem executar o conector. Fallback determinístico sempre que o modelo pula uma tool
  obrigatória. Opcional (`LLM_PROVIDER=anthropic`)
- **Fluxo de confirmação de ação destrutiva de verdade** (§7.1 regra #2), para os **4 intents
  destrutivos do catálogo** (FIN-03, NET-04, OPS-02, OPS-03): 1ª passada pede confirmação
  (`SLOT_COLLECTION → CONFIRMATION`); a resposta do cliente é interpretada como sim/não *antes* de
  qualquer classificação de intenção; "sim" chama `confirm_action(True)` (`→ EXECUTION`, só aí a
  tool destrutiva entra no allowlist) e executa de verdade; "não" cancela sem executar nada;
  resposta ambígua conta como falha de reconhecimento do slot (regra §7.1 #3) e escalona na 2ª
  tentativa. OPS-02 usa `manage_visit`, método dedicado do `ISPConnector` (ver item abaixo) — não
  reaproveita mais `create_service_order`
- **Adapters de ACS (`GenieACSAdapter`) e NMS (`ZabbixAdapter`)** (spec §4.4): implementam de
  verdade `get_cpe_diagnostics`/`reboot_cpe` (contra a NBI do GenieACS) e `get_area_incidents`
  (contra a API JSON-RPC do Zabbix) — os 3 métodos confirmadamente ausentes de qualquer ERP.
  Compostos por um novo `ConnectorHub`, que `get_connector()` sempre devolve: delega
  `get_cpe_diagnostics`/`reboot_cpe`/`get_area_incidents` para o adapter configurado
  (`ACS_PROVIDER=genieacs`/`NMS_PROVIDER=zabbix`) e cai de volta no ERP (Mock ou Hubsoft) quando
  nenhum adapter está configurado — passthrough transparente, `MockISPConnector` continua
  funcionando exatamente como antes. `massive_detection.check_massive_incident` degrada
  graciosamente (`is_massive=False`) se `get_area_incidents` levantar `NotImplementedError` ou
  `ConnectorError`, em vez de derrubar a chamada. RX power de ONT não tem path TR-181 universal
  (varia por fabricante — Huawei/ZTE/Nokia cobertos) e a correlação `olt_id` (ERP) ↔ `hostid`
  (Zabbix) é feita via tag configurável, com fallback por nome — ambas as limitações documentadas
  em [`docs/connectors/genieacs.md`](./docs/connectors/genieacs.md) e
  [`docs/connectors/zabbix.md`](./docs/connectors/zabbix.md)
- **`manage_visit`** — método dedicado do `ISPConnector` para OPS-02 (agendar/reagendar/cancelar
  visita técnica, spec §2.1), substituindo o reaproveitamento de `create_service_order`. As três
  ações da Hubsoft foram verificadas contra `ordem_servico/{agendar,reagendar,remover_agendamento}.rst`
  (github.com/hubsoftbrasil/api): nenhuma cria uma OS do zero — todas exigem uma OS já existente;
  `agendar` não recebe janela de horário (confirmado, não omissão); `reagendar` exige data/hora de
  início e fim; `remove_agendamento` exige um `id_motivo_remocao_agendamento` sem catálogo fixo
  documentado (`HUBSOFT_CANCEL_REASON_ID` no `.env`, sem valor inventado quando ausente). No
  `CallOrchestrator`, a ação (agendar/reagendar/cancelar) é decidida por palavra-chave em
  `_handle_ops_02` antes da confirmação verbal. Detalhes em
  [`docs/connectors/hubsoft.md`](./docs/connectors/hubsoft.md)
- **Slot-filling determinístico de data/hora** (`orchestrator/pt_datetime.py`) — reagendamento de
  visita (OPS-02) não depende mais de `LLM_PROVIDER=anthropic`: sem `tool_executor` configurado,
  `parse_visit_window` tenta achar um dia (relativo, dia da semana, ou data explícita) **e** um
  período/horário na mesma fala; se achar, pula direto para a confirmação, senão pergunta
  separado (`_awaiting_visit_window`, `handle_utterance` desvia pra
  `_handle_visit_window_answer` em vez de reclassificar intenção) e conta como falha de
  reconhecimento do slot a cada tentativa não reconhecida (regra §7.1 #3 — escalona após 2). Nunca
  inventa uma janela que o cliente não confirmou (spec §12); testado de ponta a ponta contra a API
  demo real (`scripts/talk.py`), não só em unitário
- **Migrações reais com Alembic** (`db/migrations/`, template async): schema gerado por autogenerate
  a partir de `db/models.py`, aplicado via `make migrate`. O boot da API real nunca cria/altera
  schema sozinho — `check_migrations_applied` confirma que a migração já rodou e recusa subir com
  mensagem clara se não tiver rodado, em vez de um erro genérico de "relation does not exist" ou
  (pior) criar tabelas silenciosamente com múltiplas réplicas no ar. Testado de ponta a ponta contra
  Postgres real: gerar → aplicar → `alembic check` (zero diff) → downgrade completo → reaplicar,
  além do boot da API falhando/subindo conforme esperado nos dois cenários
- **`DeepgramASR` e `ElevenLabsTTS` reais** (spec §4.1/§4.2), plugáveis via
  `get_asr_engine()`/`get_tts_engine()` (`ASR_PROVIDER=deepgram`/`TTS_PROVIDER=elevenlabs`):
  `DeepgramASR` fala o WebSocket de streaming da Deepgram (Nova-3, único modelo com suporte
  dedicado a `pt-BR`) com autenticação por header, resultados parciais/finais e fechamento
  gracioso (`CloseStream`); `ElevenLabsTTS` fala o streaming HTTP da ElevenLabs (modelo Flash,
  menor time-to-first-byte) com `output_format=alaw_8000` (G.711 A-law — mesmo codec de telefonia
  do Brasil). **Diferente dos outros adapters do projeto, não dá para testar contra os servidores
  reais sem contas pagas** (Deepgram/ElevenLabs não são self-hospedáveis como GenieACS/Zabbix) —
  validado só contra a documentação pública e testes com WebSocket/HTTP fake; limitações
  detalhadas (KeepAlive, boosting de vocabulário, SSML, barge-in) em
  [`docs/voice/deepgram.md`](./docs/voice/deepgram.md) e
  [`docs/voice/elevenlabs.md`](./docs/voice/elevenlabs.md)
- **`AudioSocketBridge`/`AudioSocketServer` reais** (spec §3) contra o protocolo AudioSocket do
  Asterisk — cabeçalho/tipos de mensagem (terminate, UUID, DTMF, áudio 8kHz, erro) verificados e
  **testados de ponta a ponta contra um Asterisk real via Docker** (`andrius/asterisk:20`,
  `--network host`, chamada originada via `channel originate` + `Milliwatt()` como fonte de áudio —
  sem precisar de softphone/trunk SIP): UUID do dialplan bateu exatamente, frames de 320 bytes
  (achado real — 20ms de PCM a 8kHz, o tamanho de frame padrão do Asterisk) com áudio genuíno, não
  silêncio. Diferente de Deepgram/ElevenLabs, o Asterisk é self-hospedável — deu para validar de
  verdade, não só contra documentação. `transfer()` confirmadamente não existe no protocolo
  AudioSocket puro (precisa de ARI channel redirect, não implementado). Detalhes e limitações
  (formato de áudio de saída ainda não convertido para A-law) em
  [`docs/telephony/audiosocket.md`](./docs/telephony/audiosocket.md)
- **Voice runtime próprio** (`voice/runtime.py`, spec §3) — decisão de arquitetura: nenhum
  framework externo (LiveKit Agents/Pipecat) adotado, porque ambos pressupõem um LLM conduzindo a
  conversa livremente dentro do pipeline deles, o padrão que a spec §3.2 recusa. Loop enxuto que
  liga `AudioBridge`+`ASREngine`+`CallOrchestrator`+`TTSEngine`, com escuta e fala em tasks
  concorrentes (bug real encontrado e corrigido: uma primeira versão sequencial nunca dava chance
  de barge-in interromper nada) e barge-in best-effort cancelando a síntese em andamento quando
  chega um resultado parcial novo do ASR. 190+13 testes cobrindo os 4 adapters isoladamente mais o
  runtime com fakes — **nunca testado com as 4 pontas reais simultâneas** (Asterisk real validado
  isoladamente; Deepgram/ElevenLabs sem conta paga). Detalhes em
  [`docs/voice/runtime.md`](./docs/voice/runtime.md)

Pendente — próximas fases:
1. Validar o `HubsoftConnector` contra um ambiente real do provedor piloto (a doc pública não cobre tudo — ver limitações em `docs/connectors/hubsoft.md`); escrever `IXCSoftConnector`/`VoalleConnector` se o piloto for outro ERP; validar `GenieACSAdapter`/`ZabbixAdapter`/`manage_visit` contra ACS/NMS/ERP reais do provedor (ver "Quando houver acesso a um ambiente real" nos respectivos docs)
2. `pt_datetime.py` cobre um subconjunto deliberadamente limitado de expressões de data/hora em português — validar contra transcrições reais de ligação do piloto e ampliar conforme os padrões de fala que aparecerem (ex.: "na próxima semana", "início da tarde")
3. Validar `DeepgramASR`/`ElevenLabsTTS` contra contas reais; converter o áudio de saída do TTS para o formato que `AudioSocketBridge` espera (PCM16, não A-law — ver docs/telephony/audiosocket.md); implementar `transfer()` via ARI channel redirect; testar as 4 pontas reais simultâneas e medir latência de turno contra o orçamento do §5.1; Kamailio (SBC) na frente do Asterisk
4. Parecer jurídico formal (Decreto 11.034, RGC, LGPD) antes de qualquer go-live — ver spec §6

Ver `spec-voicebot-isp.md` §10 (roadmap) e §14 (próximos passos) para o plano completo.
