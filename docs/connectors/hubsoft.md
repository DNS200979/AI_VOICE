# HubsoftConnector — checklist de implementação

`src/voxisp/connectors/hubsoft.py` está **stubado**: implementa o `Protocol`
`ISPConnector`, já plugado na fábrica (`get_connector("hubsoft")`) e na
camada de resiliência (`resilience.py`), mas cada método levanta
`NotImplementedError` até a documentação/credenciais reais da API Hubsoft
chegarem. Este documento é o checklist do que falta preencher.

## O que precisamos da Hubsoft

- [ ] Documentação da API (REST? GraphQL?) — endpoint base, versão
- [ ] Método de autenticação (assumido: OAuth2 `client_credentials` —
      confirmar; hoje `config.py` tem `hubsoft_client_id`/`hubsoft_client_secret`)
- [ ] Ambiente de sandbox/homologação com dados de teste
- [ ] Rate limits e política de paginação
- [ ] Confirmação de quais dos endpoints abaixo existem e seus schemas reais

## Mapeamento intent → endpoint (hipótese a validar)

| Método do `ISPConnector` | Endpoint Hubsoft (hipótese) | Observações |
|---|---|---|
| `find_subscriber` | `GET /clientes?cpf=...` ou `?telefone=...` | Confirmar campo de busca e se retorna múltiplos contratos por CPF |
| `get_invoices` | `GET /clientes/{id}/faturas?status=...` | Confirmar enum de status da Hubsoft vs. `InvoiceStatus` nosso |
| `issue_second_copy` | `POST /faturas/{id}/segunda-via` | Confirmar se retorna PIX copia-e-cola pronto ou só linha digitável (pode exigir geração de PIX à parte via PSP) |
| `request_trust_unlock` | `POST /clientes/{id}/desbloqueio-confianca` | Confirmar regra de elegibilidade nativa do ERP (nº de desbloqueios/mês etc. — spec FIN-03) |
| `get_connection_status` | `GET /clientes/{id}/sessao-radius` (ou via AAA separado) | Pode não existir na Hubsoft — pode precisar vir do `FreeRADIUSAdapter` (radacct) direto, não do ERP |
| `get_cpe_diagnostics` | Provavelmente **fora do Hubsoft** — vem do ACS (GenieACS/Aprecomm), não do ERP |
| `reboot_cpe` | Idem — TR-069 via ACS, não via Hubsoft |
| `list_service_orders` / `create_service_order` | `GET/POST /clientes/{id}/os` | Confirmar categorias de OS aceitas pela Hubsoft para pré-triagem (OPS-03) |
| `get_area_incidents` | Provavelmente **fora do Hubsoft** — vem do NMS (Zabbix/OLT SNMP), correlação feita em `massive_detection.py` |
| `create_protocol` | `POST /protocolos` ou gerado localmente e só registrado na Hubsoft | Confirmar se a Hubsoft emite protocolo nativamente (exigência regulatória §6.1) |

**Implicação arquitetural:** parte destas chamadas (ACS, RADIUS, NMS) não
deve vir do `HubsoftConnector` — o ERP não sabe de RX power de ONU. Quando a
doc chegar, provável que `get_cpe_diagnostics`/`reboot_cpe`/`get_area_incidents`
migrem para adapters de telemetria separados (`GenieACSAdapter`,
`FreeRADIUSAdapter`, `OLTSnmpAdapter` — spec §4.4) e o `CallOrchestrator`
passe a receber os dois (`HubsoftConnector` + adapters) em vez de um único
objeto. Isso é decisão de composição, não de interface — o `Protocol`
`ISPConnector` atual é suficiente para começar.

## Idempotência (ações destrutivas)

`reboot_cpe`, `request_trust_unlock` e qualquer alteração de vencimento
precisam de `idempotency_key` propagada até a chamada HTTP real (spec §5,
"Idempotência"). Confirmar se a API Hubsoft aceita header idempotente
(`Idempotency-Key`) nativamente ou se precisamos implementar de-dupe do
lado do conector (ex.: Redis `SETNX` com TTL, checando a chave antes de
disparar a chamada).

## Cache (spec §4.4)

TTL curto (30–120s) para dados de sessão/status — aplicar depois que os
endpoints reais e a volatilidade de cada um forem conhecidos (ex.: dado de
fatura pode cachear mais tempo que status de sessão RADIUS).

## Quando a documentação chegar

1. Preencher `HUBSOFT_BASE_URL`/`HUBSOFT_CLIENT_ID`/`HUBSOFT_CLIENT_SECRET` no `.env`
2. Implementar `_authenticate()` e `_request()` em `hubsoft.py` com o fluxo real
3. Trocar cada `raise NotImplementedError(...)` pela chamada real, mapeando o schema de resposta para os modelos Pydantic de `connectors/models.py`
4. Rodar `tests/test_mock_connector.py` adaptado (ou uma cópia parametrizada) contra `HubsoftConnector` em ambiente de sandbox — mesma bateria de contrato
5. Validar os campos de `Subscriber` (`olt_id`/`pon`/`cto_id`/`cpe_serial`) — se a Hubsoft não tiver isso, precisa vir de um adapter de NMS/ACS combinado no orquestrador
