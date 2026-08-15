# Stack local de teste prévio (GenieACS + Zabbix)

Antes de existir um ISP piloto, dá pra validar boa parte da integração de
telemetria (`GenieACSAdapter`, `ZabbixAdapter`) contra **software real** —
não mocks — usando `docker-compose.test-infra.yml`. Isso cobre exatamente
os dois adapters que `docs/connectors/genieacs.md`/`zabbix.md` descrevem;
o `HubsoftConnector` fica de fora porque a Hubsoft **não tem sandbox
público** (confirmado — credenciais de API só são liberadas para clientes
pagantes, ver `docs/connectors/hubsoft.md`).

Pré-requisito: Docker + Docker Compose. Este arquivo é separado do
`docker-compose.yml` principal (Postgres/Redis da própria aplicação, usado
por `make up`) — não faz parte do fluxo normal de dev, é só para esta
validação pontual.

**Já rodei isto de ponta a ponta** (não é só teoria) — os dois bugs reais
que apareceram já estão corrigidos no código:
- `mongo:8.0` não sobe em kernel Linux ≥6.19 (erro documentado no próprio
  MongoDB, `jira.mongodb.org/browse/SERVER-121912`) — o compose já usa
  `mongo:7.0`, que não tem esse problema.
- A NBI do GenieACS devolve `_lastInform` como string ISO 8601
  (`"2026-08-15T20:40:19.935Z"`), não epoch em milissegundos como a doc
  sugere em alguns exemplos — `GenieACSAdapter._parse_last_inform` já
  aceita os dois formatos (`connectors/genieacs.py`).

Com isso corrigido, validei contra os containers reais: diagnóstico de CPE
(`onu_status=unknown` — esperado, o simulador genérico não popula os paths
de RX power específicos de fabricante), reboot com fallback para "enfileirado"
(o `genieacs-sim` não responde ao connection request síncrono dentro do
docker network deste teste) e a mitigação de idempotência reaproveitando a
task pendente numa segunda chamada. Do lado do Zabbix: login real, host com
tag `olt_id`, um trigger `nodata()` disparando um problema de verdade sem
precisar de `zabbix_sender`, e os dois caminhos de correlação (por tag e
por fallback de nome) trazendo o mesmo incidente via `ZabbixAdapter` real.

## 1. GenieACS (ACS)

```bash
# Sobe GenieACS + MongoDB + um CPE simulado via TR-069 (perfil "testing")
docker compose -f docker-compose.test-infra.yml --profile testing up -d genieacs-mongo genieacs genieacs-sim
```

Espere ~1 minuto (healthcheck do GenieACS) e confirme que o simulador se
registrou:

```bash
curl -s http://localhost:7557/devices/ | python3 -m json.tool
```

Deve aparecer pelo menos um device com um `_deviceId._SerialNumber`. Anote
esse serial — é o `cpe_serial` que você vai usar nos comandos abaixo.

No `.env`:

```
ACS_PROVIDER=genieacs
GENIEACS_BASE_URL=http://localhost:7557
```

Rode o smoke test:

```bash
.venv/bin/python scripts/smoke_test_acs_nms.py acs <serial-anotado-acima>
.venv/bin/python scripts/smoke_test_acs_nms.py acs-reboot <serial-anotado-acima>
```

**O que isso valida de verdade:** conectividade HTTP real com a NBI,
parsing do documento TR-098/TR-181 do device, o fluxo de reboot via task
(200 vs 202) e a mitigação de idempotência (rodar `acs-reboot` duas
vezes seguidas deve reaproveitar a task pendente na segunda, não duplicar).

**O que isso NÃO valida:** os 3 paths de RX power por fabricante
(Huawei/ZTE/Nokia, ver `docs/connectors/genieacs.md`) — o simulador
genérico não necessariamente popula esses parâmetros vendor-specific, então
`onu_status` provavelmente vem `UNKNOWN` aqui mesmo com tudo funcionando.
Para validar isso de verdade é preciso um CPE real (ou popular manualmente
o parâmetro via `http://localhost:3000`, a UI web do GenieACS, em
Admin → Parameters do device) com o path exato do fabricante que o piloto
realmente usa.

## 2. Zabbix (NMS)

```bash
docker compose -f docker-compose.test-infra.yml up -d zabbix-postgres zabbix-server zabbix-web
```

Espere ~1–2 minutos e acesse `http://localhost:8080` — login padrão
`Admin` / `zabbix`.

Crie o cenário de teste pela UI:

1. **Data collection → Hosts → Create host**: nome ex. `OLT-TESTE-01`,
   grupo qualquer (ex. "Linux servers"). Na aba **Tags**, adicione
   `olt_id` = `OLT-01` (ou o valor que você quiser usar como
   `ZABBIX_OLT_TAG_KEY`/`olt_id` do assinante de teste — ver
   `docs/connectors/zabbix.md` sobre essa correlação).
2. Crie um **Item** qualquer nesse host (ex. tipo "Zabbix trapper",
   chave `teste.disponibilidade`) — precisa de ao menos um item para um
   trigger existir.
3. Crie um **Trigger** nesse item com uma expressão sempre satisfeita
   nos testes (ex. `last(/OLT-TESTE-01/teste.disponibilidade)=0`) e
   dispare o problema manualmente enviando um valor 0 via
   `zabbix_sender`, ou mais simples: **Monitoring → Problems → Create
   problem** (Zabbix ≥6.0 permite gerar um problema manual de teste
   direto pela UI, sem precisar de sender/agent).

No `.env`:

```
NMS_PROVIDER=zabbix
ZABBIX_BASE_URL=http://localhost:8080
ZABBIX_USERNAME=Admin
ZABBIX_PASSWORD=zabbix
ZABBIX_OLT_TAG_KEY=olt_id
```

Rode o smoke test:

```bash
.venv/bin/python scripts/smoke_test_acs_nms.py nms OLT-01 PON-04
```

**O que isso valida de verdade:** login via `user.login` + Bearer token,
a correlação por tag (`host.get` com `tags`), e `problem.get` trazendo o
problema criado no passo 3. **O que isso NÃO valida:** o fallback por nome
(cai nele automaticamente se você não tiver criado a tag — teste os dois
casos removendo a tag numa segunda rodada) nem o comportamento em versões
Zabbix <6.4 (auth por campo `auth`, não implementado neste adapter — ver
limitação documentada em `docs/connectors/zabbix.md`).

## 3. Encerrar a stack

```bash
docker compose -f docker-compose.test-infra.yml --profile testing down -v
```

`-v` remove os volumes (MongoDB/Postgres) — sem isso, os dados persistem
entre execuções, o que é útil se você quiser continuar de onde parou.

## O que essa stack não substitui

Isso valida o **transporte e o parsing real** dos dois adapters. Não
substitui a validação com o piloto real, que é a única forma de confirmar:
- Qual path de RX power o(s) modelo(s) de ONT do piloto realmente usa(m).
- Se o provedor consegue de fato tagear os hosts de OLT no Zabbix com
  `olt_id`, ou se a correlação vai precisar do fallback por nome.
- O comportamento real da Hubsoft (sem sandbox — só dá para testar com
  credenciais de um cliente real, ver `docs/connectors/hubsoft.md`).
