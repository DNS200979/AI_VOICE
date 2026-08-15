# HubsoftConnector

`src/voxisp/connectors/hubsoft.py` fala com a API real da Hubsoft. Os
endpoints, formatos de request/response e particularidades abaixo foram
**verificados** contra a documentação oficial:

- https://docs.hubsoft.com.br/
- https://github.com/hubsoftbrasil/api (fonte `.rst` da doc acima —
  usado aqui porque é navegável arquivo a arquivo)

Isso substitui o checklist hipotético da v1 deste documento: não é mais
suposição, é o contrato real. O que ainda falta é acesso a um ambiente de
homologação real do provedor piloto para validar contra dados de produção
(a doc pública não substitui isso).

## Autenticação

OAuth2 "password grant": `POST {base_url}/oauth/token` com `grant_type`,
`client_id`, `client_secret`, `username`, `password` — as 4 credenciais
(além da URL) exigidas em `.env` (`HUBSOFT_*`). Resposta traz `access_token`
Bearer válido por `expires_in` segundos (~30 dias na prática). O conector
reautentica sozinho quando o token expira ou quando o servidor devolve 401.

## Mapeamento intent/método → endpoint (confirmado)

| Método do `ISPConnector` | Endpoint real | Observações |
|---|---|---|
| `find_subscriber` | `GET /api/v1/integracao/cliente` (`busca=cpf_cnpj\|telefone`) | `Subscriber.id` = `id_cliente_servico` do primeiro serviço da lista (não `id_cliente` — um cliente pode ter vários serviços/planos; a v1 assume um) |
| `get_invoices` | `GET /api/v1/integracao/cliente/financeiro` (`busca=id_cliente_servico`) | Hubsoft não devolve um campo de status — inferido a partir de `data_pagamento` + vencimento |
| `issue_second_copy` | *nenhum endpoint dedicado* | `pix_copia_cola`/`linha_digitavel` já vêm no payload de `get_invoices` — o conector cacheia por `id_fatura` e resolve daí. **Não existe busca de fatura avulsa por `id_fatura`** — por isso `issue_second_copy` exige que `get_invoices` já tenha rodado nesta mesma instância |
| `request_trust_unlock` | `POST /api/v1/integracao/cliente/desbloqueio_confianca` | `dias_desbloqueio=2` (48h, spec FIN-03). Resposta não tem campo booleano de elegibilidade — inferido de `status != "success"` |
| `get_connection_status` | `GET /api/v1/integracao/cliente/extrato_conexao` (`busca=login`) | Busca por **login RADIUS**, não pelo id do assinante — o login vem de `servicos[].login` em `find_subscriber` e fica cacheado internamente. Online/offline inferido de `acctstoptime` (null = ainda conectado) |
| `get_cpe_diagnostics` | **não existe na Hubsoft** | Confirmado pela pesquisa — vem do ACS (GenieACS/Aprecomm), fora do ERP |
| `reboot_cpe` | **não existe na Hubsoft** | Idem — TR-069 via ACS |
| `list_service_orders` | `GET /api/v1/integracao/cliente/ordem_servico` (`busca=id_cliente_servico`) | Status da Hubsoft é texto livre em português (`"aguardando_agendamento"`, `"finalizado"` etc.) — mapeado para `ServiceOrderStatus` via tabela; valores não mapeados caem em `OPEN` |
| `create_service_order` | `POST /api/v1/integracao/atendimento` (`abrir_os=true`) | **Não há endpoint de "criar OS" isolado** — `ordem_servico/agendar` só *agenda* uma OS que já existe. A criação de fato acontece pelo endpoint de atendimento |
| `manage_visit` (OPS-02: agendar/reagendar/cancelar visita) | `POST /ordem_servico/agendar` \| `/reagendar` \| `/remove_agendamento` | Método dedicado — não reaproveita mais `create_service_order`. Ver seção própria abaixo |
| `get_area_incidents` | **não existe na Hubsoft** | Confirmado — vem do NMS (Zabbix/OLT SNMP), correlação feita em `massive_detection.py` |
| `create_protocol` | `POST /api/v1/integracao/atendimento` (sem `abrir_os`) | Mesmo endpoint de `create_service_order`, variando o payload — os dois convergem porque `atendimento` é o objeto que carrega o `protocolo` |
| `Subscriber.olt_id` (dentro de `find_subscriber`) | `GET /api/v1/integracao/rede/equipamento` | Sem endpoint dedicado — correlaciona `servicos[].interface.nome` (a PON do assinante, ex. `"PON5"`) com `equipamentos[].interfaces[].nome`; o equipamento dono é a OLT. Lista de equipamentos cacheada por instância (não muda por chamada); circuit breaker isolado (`_equipment_breaker`) para nunca travar chamadas críticas se `/rede/equipamento` cair |

**Confirmação da hipótese arquitetural original:** `get_cpe_diagnostics`,
`reboot_cpe` e `get_area_incidents` genuinamente não existem na Hubsoft —
não é suposição, é o que a doc real mostra. Quando esses três forem
implementados, entram como adapters de telemetria separados
(`GenieACSAdapter`, `OLTSnmpAdapter`/`ZabbixAdapter`, spec §4.4), com o
`CallOrchestrator`/`ToolExecutor` recebendo os dois (Hubsoft + adapter) em
vez de um único `ISPConnector`.

## `manage_visit` — agendar/reagendar/cancelar visita (OPS-02)

Método dedicado do `ISPConnector` (spec §2.1: "Agendamento / reagendamento
/ cancelamento de visita técnica" é um único intent do catálogo, mas as
três ações têm contratos reais bem diferentes). Substitui o reaproveitamento
de `create_service_order` que a v1 deste conector usava como simplificação
de MVP. Os três endpoints abaixo foram **verificados** contra
`docs/source/ordem_servico/{agendar,reagendar,remover_agendamento}.rst`
(github.com/hubsoftbrasil/api) — inclusive os exemplos de request/response.

| Ação | Endpoint | Payload | Observação |
|---|---|---|---|
| `schedule` | `POST /ordem_servico/agendar` | `{"id_ordem_servico": ...}` | **Confirmado, não é omissão:** este endpoint não recebe janela de horário nenhuma — só confirma o agendamento de uma OS que já tem `data_inicio_programado` definido. Nenhuma das três ações cria uma OS do zero |
| `reschedule` | `POST /ordem_servico/reagendar` | `id_ordem_servico` + `data_inicio_programado`/`hora_inicio_programado`/`data_termino_programado`/`hora_termino_programado` (mais `id_usuario_antigo`/`id_usuario_novo` opcionais, para reatribuir técnico — não expostos por este conector) | `VisitDraft.window_start`/`window_end` são obrigatórios; sem eles o conector recusa a chamada antes de bater na API |
| `cancel` | `POST /ordem_servico/remove_agendamento` | `id_ordem_servico` + `id_motivo_remocao_agendamento` + `observacao` (mín. 10 caracteres) | Ver limitação do `id_motivo_remocao_agendamento` abaixo |

**Limitação real, não contornável no código:** `id_motivo_remocao_agendamento`
não tem catálogo fixo documentado — a doc oficial diz que os valores válidos
"podem ser obtidos no endpoint `/ordem_servico/create`" do provedor (isto é,
são configuráveis por instalação Hubsoft). Este conector não inventa um
valor: `HUBSOFT_CANCEL_REASON_ID` no `.env` fica em branco por padrão, e
`manage_visit(action=cancel)` recusa a chamada com um `ConnectorError`
explícito até o provedor informar o ID correto (consultado uma vez,
manualmente, contra o `/ordem_servico/create` do ambiente real).

## Limitações conhecidas desta implementação (documentadas em `# TODO` no código)

- **`Subscriber.olt_id` — resolvido.** `find_subscriber` correlaciona
  `servicos[].interface.nome` (a PON do assinante) com
  `GET /rede/equipamento` para achar o `id_equipamento` da OLT dona
  daquela interface. Best-effort: se `/rede/equipamento` falhar ou não
  houver interface correspondente, `olt_id` fica `None` sem quebrar
  `find_subscriber` (§4.4, degradação graciosa).
- **`Subscriber.cto_id`/`cpe_serial` — confirmado que não dá para
  resolver com a Hubsoft.** Pesquisei os 3 endpoints de `rede/`
  (`equipamento`, `pop`, `zona_atendimento`) e nenhum expõe granularidade
  de CTO/caixa de emenda nem serial de ONU/CPE — não é falta de
  exploração, é ausência confirmada nesses payloads. `cpe_serial`
  provavelmente vem só do ACS (GenieACS/Aprecomm), como o restante do
  diagnóstico de CPE. **Sem `cto_id`/`get_area_incidents`, NET-03
  (correlação de massivo) continua sem funcionar com `HubsoftConnector`**
  — só com `MockISPConnector` por enquanto.
- **`Subscriber.loyalty_until`** (fidelidade contratual, spec FIN-04): não
  encontrado em `GET /cliente`. Pode estar em um endpoint de contrato não
  mapeado ainda.
- **`get_connection_status`/`create_service_order`/`create_protocol`
  exigem que `find_subscriber` tenha rodado antes** na mesma instância do
  conector (populam caches internos de login/contato). Isso é seguro no
  fluxo real do `CallOrchestrator` (identificação sempre vem primeiro),
  mas é uma dependência de ordem que uma implementação ingênua poderia
  violar — documentado nos docstrings dos métodos.
- **`ordens_servico[]` dentro da resposta de `POST /atendimento`**: a doc
  pública não mostra um exemplo completo desse array quando `abrir_os=true`
  — o parsing em `create_service_order` é defensivo (tenta `id_ordem_servico`
  ou `id`) mas precisa validação contra um ambiente real.
- **Idempotência (spec §5)**: a Hubsoft não documenta um header tipo
  `Idempotency-Key`. Chamadas que mutam estado (`POST`) usam
  `max_retries=0` na camada de resiliência (`resilience.py`) para nunca
  reenviar automaticamente uma ação que talvez já tenha sido processada.

## Quando houver acesso a um ambiente real

1. Preencher `HUBSOFT_BASE_URL`/`CLIENT_ID`/`CLIENT_SECRET`/`USERNAME`/`PASSWORD` no `.env`
2. Rodar a mesma bateria de testes de contrato do `MockISPConnector`
   (`tests/test_mock_connector.py`) contra o `HubsoftConnector` em modo
   integração (fora do CI, com credenciais reais) para validar os campos
   que a doc pública não cobre por completo
3. Validar a resolução de `olt_id` (via `/rede/equipamento`) contra o
   volume real de PONs do provedor — a correlação por nome de interface
   pode não ser 1:1 dependendo de como o provedor nomeia suas interfaces
4. Configurar `GenieACSAdapter`/`ZabbixAdapter` (`docs/connectors/genieacs.md`/
   `zabbix.md`) para resolver `cpe_serial`/`get_cpe_diagnostics`/
   `reboot_cpe`/`get_area_incidents`, que continuam ausentes do ERP puro
5. Validar o formato exato de `ordens_servico[]` em `POST /atendimento`
   com `abrir_os=true` contra uma resposta real
6. Consultar `GET /ordem_servico/create` do ambiente real do provedor para
   descobrir o `id_motivo_remocao_agendamento` correto e preencher
   `HUBSOFT_CANCEL_REASON_ID` — sem isso, `manage_visit(action=cancel)`
   não funciona
