# ZabbixAdapter

`src/voxisp/connectors/zabbix.py` fala com a **API JSON-RPC** do
[Zabbix](https://www.zabbix.com/), o NMS mais comum na base instalada de
ISPs brasileiros para monitorar OLTs/switches via SNMP. Implementa
`get_area_incidents` — o método que o `HubsoftConnector` (e, em geral,
qualquer ERP) confirmadamente não tem, porque monitoramento de rede não é
domínio de ERP.

Endpoints e formatos abaixo foram **verificados** contra a documentação
oficial da API JSON-RPC do Zabbix.

## Como o adapter se encaixa

Igual ao `GenieACSAdapter` (ver `docs/connectors/genieacs.md`): composto
pelo `ConnectorHub`, que delega `get_area_incidents` para o NMS quando
`NMS_PROVIDER=zabbix` está configurado, e cai de volta no ERP quando não —
com o ERP puro (Hubsoft) levantando `NotImplementedError`, capturado com
degradação graciosa em `massive_detection.check_massive_incident` (spec
§4.4: sem NMS configurado, a chamada não derruba o diagnóstico, só pula a
correlação de massivo e segue para o caminho individual).

## Autenticação

`POST {base_url}/api_jsonrpc.php`, corpo JSON-RPC 2.0
(`{"jsonrpc":"2.0","method":...,"params":...,"id":...}`). Login via
`user.login` com `{"username":..., "password":...}`, que devolve um token
em `result`; chamadas seguintes enviam `Authorization: Bearer <token>`.
**Esse é o comportamento documentado para versões atuais do Zabbix** —
versões anteriores à 6.4 usavam um campo `auth` dentro do corpo da
requisição em vez do header `Authorization`. Esse adapter implementa só o
esquema atual; suportar Zabbix <6.4 fica como gap conhecido, não
implementado.

## Mapeamento método → chamada JSON-RPC (confirmado)

| Método | Chamada real | Observações |
|---|---|---|
| `get_area_incidents` | `host.get` (resolver `olt_id` → `hostid`) seguido de `problem.get` (`hostids`, `severities`, `recent`) | Ver "Correlação de ID" abaixo |

`problem.get` devolve objetos com `eventid`, `objectid`, `name`,
`severity`, `clock` (timestamp Unix em string) e `r_clock` (quando
resolvido). Mapeado para `Incident.id`/`.description`/`.started_at`.

## Correlação de ID — o problema e a solução adotada

**Confirmado: não existe correlação automática entre o `olt_id` do ERP
(ex.: `id_equipamento` da Hubsoft) e o `hostid` do Zabbix.** São dois
espaços de ID completamente independentes — cada sistema numera seus
próprios objetos.

Solução adotada: recomendar que o provedor **marque** o host da OLT no
Zabbix com uma tag cuja chave é configurável (`ZABBIX_OLT_TAG_KEY`, padrão
`"olt_id"`) e valor igual ao `olt_id` do ERP. O adapter busca primeiro por
essa tag (`host.get` com `tags: [{"tag": ..., "value": olt_id}]`); se não
encontrar (provedor não configurou a tag ainda), cai para busca fuzzy por
nome (`host.get` com `search: {"host": olt_id}`) — funciona apenas se o
`olt_id` do ERP coincidir (total ou parcialmente) com o nome do host no
Zabbix, o que não é garantido. Sem tag e sem match de nome, devolve lista
vazia (degrada para "sem incidente confirmado", nunca inventa um).

## `affected_count` — aproximação, não contagem real

O Zabbix não expõe, via `problem.get`, quantas ONUs/clientes estão afetados
por um problema em um host pai (ex.: OLT down) — só que existe um problema
ativo naquele host. Para satisfazer a condição OR do spec §4.5 ("Zabbix tem
alarme ativo no elemento pai" já basta para classificar como massivo,
independente de contagem exata), o adapter usa um valor-sentinela
configurável para `Incident.affected_count` de todo incidente vindo do
Zabbix — por padrão, `MASSIVE_LOS_THRESHOLD` (mesmo limiar usado pela
correlação por LOS individual), garantindo que qualquer alarme confirmado
do NMS já dispare a classificação de massivo. **Não é uma contagem real de
afetados** — se o número exato importar (ex.: para priorizar incidentes
por tamanho), precisa de uma fonte adicional (SNMP direto na OLT, ou lógica
de negócio no Zabbix que exponha isso em outro campo).

## Quando houver acesso a um ambiente real

1. Preencher `ZABBIX_BASE_URL`/`USERNAME`/`PASSWORD`
2. Combinar com o provedor a convenção de tag (`ZABBIX_OLT_TAG_KEY`) e
   confirmar que as OLTs relevantes estão de fato tagueadas — sem isso, a
   correlação cai para o fallback de nome, que é best-effort
3. Validar se a instalação do provedor é >=6.4 (auth via `Authorization`
   header) ou anterior (auth via campo `auth` no corpo) — se for anterior,
   o adapter precisa do suporte ao esquema legado antes de funcionar
4. Decidir se `affected_count` sentinela é aceitável ou se vale a pena
   investir em uma fonte de contagem real (ex.: SNMP `ifOperStatus` por
   ONU na OLT, fora do escopo deste adapter)
