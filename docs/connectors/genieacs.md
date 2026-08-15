# GenieACSAdapter

`src/voxisp/connectors/genieacs.py` fala com a **NBI (North Bound Interface)**
do [GenieACS](https://genieacs.com/), um servidor TR-069 (ACS) open-source
comum na base instalada de ISPs brasileiros. Implementa `get_cpe_diagnostics`
e `reboot_cpe` — os dois métodos que o `HubsoftConnector` (e, em geral,
qualquer ERP) confirmadamente não tem, porque não fazem parte do domínio de
um ERP: são gerenciamento de CPE via TR-069.

Endpoints e formatos abaixo foram **verificados** contra:

- https://docs.genieacs.com/en/stable/api-reference.html
- Fóruns oficiais/issues do GenieACS (para os pontos que a doc de referência
  não cobre — ver "Limitações" abaixo)

## Como o adapter se encaixa

`GenieACSAdapter` não implementa `ISPConnector` sozinho — ele é composto
pelo `ConnectorHub` (`connectors/hub.py`), que delega `get_cpe_diagnostics`/
`reboot_cpe` para o ACS quando `ACS_PROVIDER=genieacs` está configurado, e
cai de volta no ERP (`MockISPConnector`/`HubsoftConnector`) quando não está
— ver spec §4.4, o "ISP Connector Hub" já prevê ERP/ACS/AAA/NMS como fontes
distintas atrás de uma única interface.

## Autenticação

A NBI do GenieACS **não documenta um mecanismo de autenticação nativo** — em
produção normalmente fica atrás de um proxy reverso com HTTP Basic Auth (ou
sem autenticação nenhuma, em rede interna). O adapter suporta Basic Auth
opcional via `GENIEACS_USERNAME`/`GENIEACS_PASSWORD`; se vazios, as
requisições saem sem `Authorization`.

## Mapeamento método → endpoint (confirmado)

| Método | Endpoint real | Observações |
|---|---|---|
| `get_cpe_diagnostics` | `GET /devices/?query={"_deviceId._SerialNumber":"<serial>"}` | Documento do device é a árvore de parâmetros TR-098/TR-181; cada folha é `{"_value":..., "_timestamp":..., "_type":...}` |
| `reboot_cpe` | `POST /devices/<id>/tasks?connection_request` com `{"name":"reboot"}` | HTTP 200 = executado imediatamente (connection request TR-069 funcionou); HTTP 202 = enfileirado (CPE offline, executa no próximo `Inform`) |
| checagem de reboot pendente | `GET /tasks/?query={"device":"<id>","name":"reboot"}` | Rodado antes de enfileirar um novo reboot — ver "Idempotência" abaixo |

## Potência óptica (RX power) — sem padrão universal

**Confirmado, não é falta de exploração:** o TR-098/TR-181 padrão **não tem
um parâmetro universal de potência óptica RX**. Cada fabricante de ONT
implementou o seu próprio, fora da árvore padrão:

| Fabricante | Path |
|---|---|
| Huawei | `InternetGatewayDevice.WANDevice.1.X_GponInterafceConfig.RXPower` (o typo "Interafce" é do próprio fabricante, confirmado em fórum oficial — não é erro de digitação deste código) |
| ZTE / CT-COM | `InternetGatewayDevice.WANDevice.1.X_CT-COM_GponInterfaceConfig.RXPower` |
| Nokia / Alcatel-Lucent | `InternetGatewayDevice.X_ALU_OntOpticalParam.RXPower` |

O adapter tenta os três paths nessa ordem e usa o primeiro que existir no
documento do device. Se nenhum bater (fabricante não coberto, ou parâmetro
ainda não coletado pelo ACS), `rx_power_dbm` fica `None` e `onu_status` vira
`ONUStatus.UNKNOWN` — **nunca inventa um valor** (spec §12: não alucinar
dado técnico). `GENIEACS_RX_POWER_LOS_THRESHOLD_DBM` (padrão -28.0 dBm)
define abaixo de que valor o adapter classifica `ONUStatus.LOS`.

WiFi (`wifi_channel`/`wifi_client_count`) **são** parâmetros TR-098 padrão
(`WLANConfiguration.1.Channel`/`.TotalAssociations`) — sem essa variação por
fabricante.

## Idempotência (spec §5)

A NBI do GenieACS **não tem chave de idempotência nativa** para tasks — dois
`POST /devices/<id>/tasks` com `{"name":"reboot"}` criam duas tasks de
reboot distintas. Mitigação implementada: antes de enfileirar, o adapter
consulta `GET /tasks/` filtrando por `device` + `name=reboot`; se já existir
uma task de reboot pendente para o CPE, devolve sucesso reaproveitando-a em
vez de duplicar. Não é uma garantia perfeita (race condition entre a
consulta e o POST é teoricamente possível), mas cobre o caso comum de
reenvio por timeout/retry do lado do orquestrador.

## Quando houver acesso a um ambiente real

1. Preencher `GENIEACS_BASE_URL` (e `USERNAME`/`PASSWORD` se o proxy exigir)
2. Validar contra os modelos de ONT reais do provedor **quais** dos 3 paths
   de RX power realmente respondem — a lista acima cobre os 3 fabricantes
   mais citados nos fóruns do GenieACS, mas não é exaustiva
3. Confirmar se o proxy de produção do provedor usa Basic Auth, outro
   esquema, ou nenhum — ajustar `GenieACSAdapter.__init__` se for outro
4. Validar o comportamento real de HTTP 202 (task enfileirada) — a doc
   descreve o campo `connection_request`, mas o tempo de espera antes de
   desistir da connection request síncrona é configuração do próprio
   GenieACS (`CWMP_CONNECTION_REQUEST_TIMEOUT`), não deste adapter
