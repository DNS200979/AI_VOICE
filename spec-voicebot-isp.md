# Especificação Técnica — Atendente Virtual de Voz para ISPs

**Codinome:** VOX-ISP
**Versão:** 0.1 (rascunho para discussão)
**Autor:** DNS-TI Consultoria
**Data:** Agosto/2026

---

## 1. Contexto e objetivo

Provedores regionais brasileiros (10k–200k assinantes) concentram entre **60% e 75% do volume de chamadas** em um punhado de intenções repetitivas:

| Intenção | % típico do volume | Resolvível sem humano? |
|---|---|---|
| 2ª via de boleto / consulta financeira | 25–35% | Sim, 100% |
| "Estou sem internet" | 20–30% | Sim, 60–70% |
| "Internet lenta / oscilando" | 8–12% | Parcial |
| Status / agendamento de OS | 8–12% | Sim |
| Desbloqueio de confiança | 5–8% | Sim, 100% |
| Mudança de plano / comercial | 4–6% | Parcial (transbordo) |
| Cancelamento | 3–5% | **Não** (obrigatório humano) |
| Reclamação / Anatel | 2–4% | **Não** |

**Objetivo do produto:** absorver as intenções da faixa "Sim" com resolução total (containment), e entregar as demais ao atendente humano **já com contexto carregado**, reduzindo o AHT do humano.

### Meta de negócio (12 meses pós go-live)

| KPI | Baseline típico | Meta |
|---|---|---|
| Taxa de contenção (resolvido sem humano) | 0% (URA burra: ~10%) | **55–65%** |
| AHT das chamadas transbordadas | 100% | −25% (contexto pré-carregado) |
| Custo por chamada atendida | R$ 3,50–6,00 | R$ 0,80–1,50 |
| Nível de serviço (atendida em 60s) | 70–85% | >98% (IA não tem fila) |
| CSAT pós-chamada IA | — | ≥ 4,0/5,0 |

> ⚠️ Os percentuais acima são referências de mercado. **Fase 0 do projeto existe justamente para medir o baseline real do ISP** antes de prometer número.

---

## 2. Escopo funcional

### 2.1 Tier 0 — Auto-resolução completa (sem humano)

**Financeiro**
- `FIN-01` Consulta de faturas em aberto, valor e vencimento
- `FIN-02` Envio de 2ª via: PIX copia-e-cola + linha digitável via WhatsApp/SMS; ditar linha digitável em blocos de 5 dígitos como fallback
- `FIN-03` Desbloqueio de confiança / promessa de pagamento (com regra de elegibilidade do ERP)
- `FIN-04` Consulta de plano contratado, data de adesão, fidelidade
- `FIN-05` Alteração de data de vencimento (se o ERP permitir)

**Conectividade**
- `NET-01` Diagnóstico de sessão: consulta RADIUS/PPPoE — sessão ativa? último logoff? motivo de desconexão?
- `NET-02` Diagnóstico óptico: RX power do ONU, status (Online / LOS / Dying Gasp / Power Off) via ACS ou OLT
- `NET-03` **Correlação de massivo**: se ≥ N ONUs da mesma PON/OLT/CTO estão em LOS → informar incidente, dar previsão, registrar contato e encerrar. Este item sozinho salva o call center em dia de rompimento.
- `NET-04` Reboot remoto do CPE via TR-069 (`Device.Reboot()`), com confirmação verbal explícita
- `NET-05` Teste de throughput agendado / consulta do último resultado (TR-143 ou plataforma de VCS)
- `NET-06` Diagnóstico de Wi-Fi: canal, congestionamento, nº de clientes conectados, sugestão de otimização

**Operacional**
- `OPS-01` Status de OS aberta (data, janela, técnico designado)
- `OPS-02` Agendamento / reagendamento / cancelamento de visita técnica
- `OPS-03` Abertura de OS com pré-triagem (categoria já classificada pela IA)
- `OPS-04` Informação de manutenção programada na região do assinante
- `OPS-05` Emissão e leitura de protocolo (obrigatório por regulação)

### 2.2 Tier 1 — Assistido, com transbordo contextualizado
- `ESC-01` Cancelamento → **rota obrigatória para humano**, sem tentativa de retenção pela IA
- `ESC-02` Reclamação formal / menção a Anatel / Procon → humano imediato, flag de prioridade
- `ESC-03` Falha física confirmada (fibra rompida no drop) → abre OS + transfere se cliente insistir
- `ESC-04` Solicitação explícita de atendente ("quero falar com uma pessoa", DTMF `0`) → transbordo em qualquer ponto do fluxo
- `ESC-05` Falha de reconhecimento 2× consecutivas → transbordo automático
- `ESC-06` Detecção de estresse/raiva no áudio (prosódia + léxico) → transbordo antecipado

### 2.3 Tier 2 — Ativo (outbound), fase posterior
- `OUT-01` Lembrete de vencimento / cobrança amigável (respeitar horário legal e listas de bloqueio)
- `OUT-02` Confirmação de visita técnica D-1
- `OUT-03` Pesquisa de satisfação / NPS pós-atendimento
- `OUT-04` Aviso proativo de incidente massivo para a base afetada
- `OUT-05` Campanha de upsell / renovação (validar com jurídico e regras de telemarketing)

### 2.4 Fora de escopo (v1)
- Venda/contratação de novo plano com assinatura de contrato
- Alteração de titularidade
- Ditar senha do Wi-Fi por voz — ver §6.3

---

## 3. Arquitetura

```
┌──────────────────────────────────────────────────────────────────┐
│  BORDA TELEFÔNICA                                                │
│  SIP Trunk (operadora) → Kamailio (SBC) → Asterisk 20            │
│  Codec G.711a / Opus · DTMF RFC2833 · SRTP                       │
└────────────────────────┬─────────────────────────────────────────┘
                         │ AudioSocket / ARI externalMedia (PCM 8k)
┌────────────────────────▼─────────────────────────────────────────┐
│  VOICE RUNTIME  (LiveKit Agents ou Pipecat)                      │
│  ├─ VAD (Silero) + endpointing semântico                         │
│  ├─ Barge-in (interrupção da síntese)                            │
│  ├─ Jitter buffer / resampling 8k↔16k                            │
│  └─ Gerenciador de turno                                         │
└──┬──────────────┬──────────────────────────┬─────────────────────┘
   │              │                          │
┌──▼────────┐  ┌──▼──────────────────────┐  ┌▼──────────┐
│ ASR (STT) │  │  ORQUESTRADOR (cérebro) │  │ TTS       │
│ streaming │─▶│  FSM + LLM tool-calling │─▶│ streaming │
│ pt-BR     │  │  + guardrails           │  │ pt-BR     │
└───────────┘  └──┬──────────────────────┘  └───────────┘
                  │ function calls
┌─────────────────▼────────────────────────────────────────────────┐
│  ISP CONNECTOR HUB  (o diferencial competitivo)                  │
│  ┌──────────┬──────────┬──────────┬──────────┬────────────────┐  │
│  │ ERP      │ ACS      │ AAA      │ NMS      │ Notificação    │  │
│  │ Hubsoft  │ Aprecomm │ FreeRAD. │ Zabbix   │ WhatsApp API   │  │
│  │ IXC      │ VCS      │ Accel-PPP│ OLT SNMP │ SMS            │  │
│  │ SGP      │ GenieACS │ radacct  │ Netbox   │ E-mail         │  │
│  │ Voalle   │ Axiros   │          │          │                │  │
│  └──────────┴──────────┴──────────┴──────────┴────────────────┘  │
│  Cache Redis (TTL 30–120s) · Circuit breaker · Retry com backoff │
└──────────────────────────────────────────────────────────────────┘
                  │
┌─────────────────▼────────────────────────────────────────────────┐
│  PERSISTÊNCIA & OBSERVABILIDADE                                  │
│  PostgreSQL/Supabase · Redis (sessão) · S3 (áudio)               │
│  Langfuse/OTel (trace por turno) · Grafana (contenção, latência) │
└──────────────────────────────────────────────────────────────────┘
```

### 3.1 Decisão de arquitetura: pipeline cascata vs. speech-to-speech

| Critério | Cascata (STT→LLM→TTS) | S2S (Realtime API) |
|---|---|---|
| Latência | 600–1000 ms | 300–600 ms |
| Controle de fluxo | **Alto** (FSM explícita) | Baixo |
| Auditoria / transcrição fiel | **Nativa** | Requer transcrição paralela |
| Trocar voz/marca | Fácil | Limitado |
| Custo | Menor | Maior |
| Compliance / logs regulatórios | **Melhor** | Pior |

**Recomendação:** cascata para a v1. O ganho de latência do S2S não compensa a perda de controle num domínio regulado que executa ações destrutivas (reboot, desbloqueio, alteração de vencimento).

### 3.2 O padrão que evita o desastre: FSM + LLM, não LLM solto

O LLM **não** dirige a conversa livremente. Ele atua em três papéis delimitados:

1. **Classificador de intenção** — mapeia a fala para um dos ~25 intents catalogados
2. **Extrator de entidades** — CPF, protocolo, endereço, data
3. **Redator do turno** — gera a frase de resposta a partir de um *slot* de dados já resolvido pela FSM

A **máquina de estados** decide o que pode ser executado, em que ordem, e o que exige confirmação. O LLM nunca chama uma tool destrutiva sem que a FSM esteja no estado que a autoriza.

```
[Saudação] → [Identificação] → [Intenção] → [Coleta de slots]
                                    ↓
                          [Execução da ação] → [Confirmação] → [Encerramento]
                                    ↓ falha / regra / pedido
                              [Transbordo com contexto]
```

---

## 4. Componentes — requisitos detalhados

### 4.1 ASR (Speech-to-Text)

| Requisito | Especificação |
|---|---|
| Modo | Streaming, resultados parciais |
| Idioma | pt-BR, robusto a sotaque sulista/nordestino |
| WER alvo | ≤ 12% em áudio telefônico 8 kHz |
| Latência de finalização | ≤ 300 ms após fim de fala |
| Boosting de vocabulário | ONU, ONT, roteador, PPPoE, boleto, "sem sinal", "luz vermelha", nomes de bairros e ruas da área de cobertura, nome comercial do ISP |
| Números | ITN ativa — "quatro sete dois" → `472`; validação de CPF por dígito verificador |
| Fallback | DTMF para CPF/contrato após 1 falha |

**Candidatos:** Deepgram Nova (cloud, melhor custo/latência) · Azure Speech (bom pt-BR, presença BR) · NVIDIA Riva/Parakeet ou Whisper large-v3 fine-tuned (on-prem, quando o ISP exigir dado local).

### 4.2 TTS (Text-to-Speech)

| Requisito | Especificação |
|---|---|
| Time-to-first-byte | ≤ 200 ms |
| Streaming | Obrigatório (síntese por sentença) |
| Interrupção | Cancelamento imediato do buffer no barge-in |
| SSML | Valores em reais, datas, linha digitável em grupos, pausas |
| Voz | Voz de marca do ISP; **declarar que é assistente virtual** |

**Candidatos:** ElevenLabs Flash · Cartesia Sonic · Azure Neural (`pt-BR-FranciscaNeural` etc.) · Google Chirp3-HD.

### 4.3 Orquestrador

- **Runtime:** Python 3.12 + FastAPI + asyncio
- **LLM:** modelo rápido para classificação/redação (Claude Haiku, GPT-4o-mini, Gemini Flash); modelo maior só para o classificador em casos ambíguos
- **Prompt caching** obrigatório — o system prompt com catálogo de tools é grande e repetido a cada turno
- **Timeout duro:** 1,5 s para o LLM. Estourou → frase de espera pré-gravada ("Só um instante, estou consultando…") enquanto continua
- **Guardrails:** allowlist de tools por estado; validação de schema na saída; bloqueio de qualquer conteúdo fora do domínio ISP

### 4.4 ISP Connector Hub

Cada conector implementa a mesma interface abstrata, o que permite vender o mesmo produto para provedores com ERPs diferentes:

```python
class ISPConnector(Protocol):
    async def find_subscriber(self, cpf: str | None, phone: str | None) -> Subscriber
    async def get_invoices(self, subscriber_id: str, status: str) -> list[Invoice]
    async def issue_second_copy(self, invoice_id: str) -> PaymentPayload   # PIX + linha digitável
    async def request_trust_unlock(self, subscriber_id: str) -> UnlockResult
    async def get_connection_status(self, subscriber_id: str) -> ConnectionStatus
    async def get_cpe_diagnostics(self, cpe_serial: str) -> CPEDiagnostics
    async def reboot_cpe(self, cpe_serial: str) -> ActionResult
    async def list_service_orders(self, subscriber_id: str) -> list[ServiceOrder]
    async def create_service_order(self, payload: SODraft) -> ServiceOrder
    async def get_area_incidents(self, olt_id: str, pon: str) -> list[Incident]
    async def create_protocol(self, subscriber_id: str, summary: str) -> Protocol
```

**Implementações previstas:** `HubsoftConnector`, `IXCSoftConnector`, `SGPConnector`, `VoalleConnector`, `MKSolutionsConnector`.

**Fontes de telemetria:** `AprecommVCSAdapter` (GraphQL), `GenieACSAdapter`, `FreeRADIUSAdapter` (query em `radacct`), `ZabbixAdapter` (API JSON-RPC), `OLTSnmpAdapter` (Huawei / ZTE / Nokia / FiberHome).

**Regras de resiliência:** timeout 2 s por chamada externa · circuit breaker (5 falhas → aberto por 30 s) · cache Redis com TTL curto para dados de sessão · degradação graciosa (se o ACS cair, ainda responde financeiro).

### 4.5 Detecção de massivo — algoritmo

```
1. Identificar assinante → obter OLT, slot, PON, CTO
2. Contar ONUs em LOS/Dying Gasp na mesma PON nos últimos 15 min
3. SE (contagem >= 3) OU (Zabbix tem alarme ativo no elemento pai):
     → classificar como MASSIVO
     → responder com previsão de normalização (se houver no ERP)
     → registrar contato no incidente (para relatório de impacto)
     → NÃO abrir OS individual
     → oferecer aviso por WhatsApp quando normalizar
4. SENÃO → seguir para diagnóstico individual
```

Esse bloco é o de maior ROI: em dia de rompimento, o volume de chamadas explode e todas têm a mesma resposta.

---

## 5. Requisitos não-funcionais

| Categoria | Requisito |
|---|---|
| **Latência de turno** (fim da fala → 1º áudio de resposta) | p50 ≤ 700 ms · p95 ≤ 1200 ms · nunca > 2500 ms sem áudio de espera |
| **Barge-in** | Obrigatório, corte em ≤ 150 ms |
| **Disponibilidade** | 99,9% mensal. Falha do agente → *failover* para URA tradicional/fila humana, nunca chamada caída |
| **Concorrência** | Dimensionar em canais simultâneos. Regra de bolso: 1 canal para cada 400–600 assinantes no pico. ISP de 30k → ~60 canais |
| **Escalabilidade** | Horizontal por worker de chamada; 1 worker ≈ 1 chamada; ~150–250 MB RAM/chamada |
| **Segurança** | TLS 1.3 · SRTP · segredos em Vault/Doppler · multi-tenant com RLS no Postgres · rede isolada para acesso ao ERP do ISP |
| **Retenção** | Áudio 90 dias · transcrição e metadados 5 anos (validar com jurídico) |
| **Idempotência** | Toda ação destrutiva com chave de idempotência para evitar reboot duplicado em retry |

### 5.1 Orçamento de latência (alvo p50)

| Etapa | ms |
|---|---|
| Endpointing (VAD + silêncio) | 250 |
| ASR finalização | 120 |
| Classificação + tool call (LLM) | 180 |
| Chamada ao ERP/ACS (com cache) | 90 |
| Redação do turno (LLM) | 150 |
| TTS primeiro byte | 180 |
| Rede/telefonia | 60 |
| **Total** | **~1030 ms** |

Otimizações: iniciar a consulta ao ERP em paralelo à finalização do ASR quando a intenção já estiver clara pelo parcial; pré-sintetizar frases fixas ("Só um momento…"); *speculative execution* para intenções de alta confiança.

---

## 6. Compliance — Brasil

> ⚠️ **Não sou advogado.** Esta seção lista os pontos que precisam de validação jurídica formal antes do go-live. O risco regulatório aqui é o maior risco do projeto, acima do risco técnico.

### 6.1 Decreto 11.034/2022 ("Lei do SAC") — pontos que impactam o design

- **Disponibilidade:** SAC ininterrupto (24×7) em ao menos um canal; atendimento telefônico humano com carga horária mínima diária. Um bot 24×7 **não substitui** a exigência de humano.
- **Acesso ao atendente:** a opção de falar com atendente e a de cancelamento devem estar entre as **primeiras opções** do menu. O acesso ao humano **não pode ser condicionado** ao fornecimento prévio de dados.
- **Protocolo:** emitido no início do atendimento, informado ao consumidor e disponível para consulta.
- **Cancelamento:** processado imediatamente, sem retenção obrigatória, sem transferência para "setor de retenção" como barreira.
- **Vedação de transferência sem resolução** e de repetição de demanda a cada transferência — daí o requisito de *contexto carregado no transbordo*.

### 6.2 Anatel — RGC (Res. 632/2014)
- Atendimento por atendente humano quando solicitado, dentro do prazo regulamentar (referência de 60 s).
- Canal telefônico gratuito e obrigatório.
- Registro e rastreabilidade das solicitações.

### 6.3 LGPD

| Ponto | Tratamento |
|---|---|
| Base legal | Execução de contrato (art. 7º, V) para operações de suporte; consentimento para outbound de marketing |
| **Biometria de voz** | Dado sensível (art. 5º, II). **Recomendação: não usar na v1.** Se usar, consentimento específico e destacado |
| Gravação | Aviso no início da chamada ("esta ligação está sendo gravada e atendida por assistente virtual") |
| Autenticação | ANI **nunca sozinho** (spoofing de origem é trivial). Mínimo: ANI + CPF + 1 fator (data de nascimento ou token SMS) |
| **Senha do Wi-Fi** | **Não ditar por voz.** Padrão TR-181 `KeyPassphrase` é write-only na maioria dos CPEs, o que já bloqueia tecnicamente a leitura. Fluxo aprovado: permitir **alteração** com envio da nova senha por WhatsApp/SMS ao número cadastrado — nunca ao número chamador se forem diferentes |
| Minimização | Não persistir CPF em claro nos logs; mascarar em transcrições |
| Retenção | Política escrita, com expurgo automatizado |

### 6.4 Transparência
Declarar que é assistente virtual na saudação. Não usar recursos para simular humano (respiração, "hmm", muletas). Além do risco regulatório, quando o cliente descobre que foi enganado, o CSAT despenca.

---

## 7. Design conversacional

### 7.1 Regras invioláveis
1. `0` (DTMF) ou "atendente"/"pessoa"/"humano" → transbordo imediato, em qualquer estado
2. Ação destrutiva (reboot, desbloqueio, alteração de vencimento, agendamento) → confirmação verbal explícita
3. Máx. 2 falhas de reconhecimento no mesmo slot → transbordo
4. Máx. 3 turnos sem progresso de estado → transbordo
5. Nenhum silêncio > 1,2 s sem áudio de preenchimento
6. Nenhuma resposta com mais de ~25 palavras sem pausa para o cliente falar

### 7.2 Exemplo de fluxo — "estou sem internet"

```
IA:  Provedor X, assistente virtual. Sua ligação é gravada.
     Me diga seu CPF, por favor.
CLI: 123.456.789-00
IA:  [valida DV, consulta ERP] Obrigada, Sr. João. Como posso ajudar?
CLI: Minha internet parou.
IA:  [NET-01 sessão RADIUS: offline há 40min]
     [NET-02 ONU: LOS]
     [NET-03 correlação: 7 ONUs em LOS na mesma PON]
     Identifiquei uma interrupção que afeta sua região desde as 14h10.
     Nossa equipe já está no local, com previsão de normalização
     até as 17h. Quer que eu avise no seu WhatsApp assim que voltar?
CLI: Quero sim.
IA:  Combinado. Seu protocolo é 2026-0814-04471. Mais alguma coisa?
```

Caso individual (sem massivo), a árvore segue: LOS → verificar cabo/luz vermelha → se persistir, abre OS. Sessão ativa mas cliente sem acesso → problema no Wi-Fi/CPE → oferecer reboot remoto.

### 7.3 Payload do transbordo
Quando transfere, o atendente humano recebe na tela do ERP, antes de atender:
- Identificação já validada do assinante
- Transcrição resumida do que já foi dito
- Diagnósticos já executados e seus resultados
- Intenção classificada + motivo do transbordo
- Protocolo já emitido

Sem isso, o transbordo dobra a irritação do cliente ("já falei tudo isso pro robô") e o projeto morre no NPS.

---

## 8. Modelo de dados (núcleo)

```sql
call            (id, tenant_id, ani, dnis, started_at, ended_at, duration_s,
                 subscriber_id, outcome, contained bool, escalated_to,
                 protocol, recording_url, csat)
turn            (id, call_id, seq, role, transcript, asr_confidence,
                 intent, entities jsonb, latency_ms jsonb, tokens jsonb)
action          (id, call_id, tool_name, params jsonb, result jsonb,
                 status, idempotency_key, executed_at)
escalation      (id, call_id, reason, queue, context_payload jsonb, at)
incident_link   (id, call_id, incident_id, source)   -- massivo
tenant_config   (id, name, connectors jsonb, persona jsonb, sla jsonb)
```

Índices essenciais: `call(tenant_id, started_at)`, `call(subscriber_id)`, `turn(call_id, seq)`, `action(idempotency_key)`.

---

## 9. Observabilidade e melhoria contínua

**Dashboard operacional (Grafana):** contenção por intenção · latência p50/p95 por etapa · taxa de transbordo por motivo · chamadas abandonadas · erro de conector por integração · custo/minuto realizado.

**Loop de qualidade semanal:**
1. Amostrar 50 chamadas transbordadas por falha de compreensão
2. Rotular manualmente o intent correto
3. Alimentar o conjunto de avaliação
4. Ajustar prompts/boosting/FSM
5. Rodar regressão sobre o conjunto antes de subir para produção

**Conjunto de regressão:** mínimo 200 diálogos rotulados, com asserções sobre intent classificado, tools chamadas e ausência de tool destrutiva indevida. Nenhum deploy sem passar 100% nas asserções de segurança.

---

## 10. Roadmap de implantação

| Fase | Duração | Entrega | Contenção acumulada |
|---|---|---|---|
| **0 — Descoberta** | 3–4 sem | Mineração de 3–6 meses de gravações e tickets; taxonomia de intents; baseline de KPIs; mapeamento das APIs do ERP | — |
| **1 — MVP financeiro + massivo** | 6–8 sem | Identificação, FIN-01→03, NET-01→03, protocolo, transbordo | **35–45%** |
| **2 — Técnico** | 5–6 sem | NET-04→06, OPS-01→04, diagnóstico individual completo | **55–65%** |
| **3 — Ativo** | 4–5 sem | Outbound (OUT-01→04), analytics, painel de gestão | — |
| **4 — Multi-tenant** | 6 sem | Conectores adicionais (IXC, SGP, Voalle), onboarding self-service | — |

**Estratégia de piloto:** rotear 10% das chamadas por *round-robin* na URA, comparando contenção, AHT e CSAT contra o fluxo atual. Subir para 30% → 60% → 100% conforme os KPIs sustentarem.

---

## 11. Estimativa de custo operacional

Ordem de grandeza por **minuto de chamada** (validar com cotação real):

| Item | Custo/min (USD) | Custo/min (BRL ~5,4) |
|---|---|---|
| STT streaming | 0,004–0,010 | R$ 0,02–0,05 |
| LLM (com cache de prompt) | 0,008–0,025 | R$ 0,04–0,14 |
| TTS | 0,010–0,050 | R$ 0,05–0,27 |
| Infraestrutura (compute, DB) | 0,003–0,008 | R$ 0,02–0,04 |
| Telefonia (SIP) | — | R$ 0,02–0,08 |
| **Total** | | **R$ 0,15–0,58** |

Comparativo: um PA (posição de atendimento) custa aproximadamente R$ 4.000–6.000/mês com encargos e infra. A ~140 h úteis/mês com 65% de ocupação, o minuto humano efetivo sai em torno de **R$ 0,70–1,10**, sem contar supervisão, treinamento, turnover e o custo do cliente esperando na fila.

O ganho real não é só o delta por minuto — é a eliminação do pico. A IA atende 200 chamadas simultâneas no dia do rompimento sem contratar ninguém.

---

## 12. Riscos

| Risco | Impacto | Mitigação |
|---|---|---|
| API do ERP instável ou mal documentada | Alto | Fase 0 valida as APIs antes de qualquer promessa; camada de cache e degradação graciosa |
| Não conformidade com Decreto 11.034 / RGC | **Crítico** | Parecer jurídico antes do go-live; caminho para humano sempre disponível; auditoria de fluxos |
| Rejeição do cliente ao "robô" | Alto | Declarar que é IA; transbordo em 1 palavra; medir CSAT desde o piloto |
| Alucinação em informação financeira | **Crítico** | LLM nunca gera número — valores vêm de slot preenchido pela FSM a partir do ERP; template travado |
| Latência acima do aceitável | Médio | Orçamento de latência monitorado por etapa; áudio de preenchimento; região de inferência em SP |
| Vazamento de dado pessoal em log | Alto | Mascaramento de CPF na ingestão; revisão de retenção; DPO |
| Dependência de fornecedor único de LLM/TTS | Médio | Camada de abstração por provedor; segundo fornecedor homologado |

---

## 13. Diferencial competitivo

Plataformas genéricas de voice AI (Vapi, Retell, Total IP, Zenvia) entregam o *runtime* de voz. Nenhuma entrega:

1. **Conectores nativos para ERPs de provedor** (Hubsoft, IXC, SGP, Voalle, MK)
2. **Telemetria de rede em tempo real** dentro da conversa — RADIUS, TR-069, OLT, Zabbix
3. **Correlação de massivo** — o caso de uso de maior ROI e o mais difícil de replicar
4. **Compliance de telecom brasileiro** embutido (RGC, SAC, protocolo, LGPD)

O fosso não está no voice AI. Está na camada de integração e no conhecimento de domínio. Um provedor consegue plugar o Retell num prompt em uma tarde; não consegue construir a correlação PON→LOS→incidente→previsão.

---

## 14. Próximos passos

1. Escolher o ISP piloto e obter acesso de leitura ao ERP e ao ACS
2. Extrair 3 meses de histórico de chamadas para a taxonomia de intents
3. Prototipar o caminho crítico ponta a ponta — 2ª via de boleto — e medir latência real em telefonia brasileira
4. Levantar parecer jurídico sobre Decreto 11.034 e RGC aplicados ao fluxo desenhado
5. Definir o modelo comercial: licença por assinante/mês, por minuto, ou por chamada contida
