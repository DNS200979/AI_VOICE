"""GenieACSAdapter — ACS real (TR-069), contra a API NBI do GenieACS.

Endpoints e formatos verificados em:
- https://docs.genieacs.com/en/stable/api-reference.html
- https://github.com/genieacs/genieacs/wiki/api-reference
- https://forum.genieacs.com/ (várias threads sobre potência óptica —
  citadas abaixo, porque é onde mora a maior lacuna real)

GenieACS é open-source, por isso a escolha como primeiro adapter de ACS
(spec §4.4 lista GenieACS/Aprecomm como candidatos) — Aprecomm é comercial
e não tem doc pública de API para pesquisar da mesma forma.

Este módulo implementa só o que `get_cpe_diagnostics`/`reboot_cpe`
precisam — não é um `ISPConnector` completo. É composto pelo
`ConnectorHub` (`connectors/hub.py`), que decide se usa este adapter ou
cai no que o ERP escolhido já oferecer.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from voxisp.connectors.base import ConnectorError
from voxisp.connectors.models import ActionResult, CPEDiagnostics, ONUStatus
from voxisp.connectors.resilience import CircuitBreaker, call_with_resilience

# NÃO existe um caminho TR-181 padrão universal para potência óptica —
# confirmado por múltiplas threads no fórum oficial do GenieACS. Cada
# fabricante de ONT usa uma extensão proprietária diferente. Esta lista é
# best-effort com os fabricantes mais citados; PRECISA ser validada contra
# os modelos de ONT do provedor real (ver docs/connectors/genieacs.md).
_RX_POWER_PATHS: tuple[str, ...] = (
    "InternetGatewayDevice.WANDevice.1.X_GponInterafceConfig.RXPower",  # Huawei (o typo é do fabricante mesmo)
    "InternetGatewayDevice.WANDevice.1.X_CT-COM_GponInterfaceConfig.RXPower",  # ZTE / CT-COM
    "InternetGatewayDevice.X_ALU_OntOpticalParam.RXPower",  # Nokia / Alcatel-Lucent
)

# Estes dois SÃO parâmetros TR-098 padrão, bem documentados — confiança alta.
_WIFI_CHANNEL_PATH = "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.Channel"
_WIFI_CLIENTS_PATH = "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.TotalAssociations"


class GenieACSNotConfiguredError(Exception):
    """`GENIEACS_BASE_URL` ausente do `.env` (com `ACS_PROVIDER=genieacs`)."""


class GenieACSAdapter:
    """Diagnóstico e reboot de CPE via GenieACS — spec §4.4.

    Particularidades reais que moldaram este código:

    - A API NBI não documenta autenticação própria — em produção normalmente
      fica atrás de VPN/firewall ou um proxy reverso com auth própria (ex.
      basic auth via nginx). Por isso o construtor aceita `username`/
      `password` opcionais para HTTP Basic, sem assumir que são obrigatórios.
    - Potência óptica (RX power) não tem caminho TR-181 padrão — ver
      `_RX_POWER_PATHS` acima. `onu_status` é inferido: sem RX power lido =
      `UNKNOWN`; abaixo do limiar configurado = `LOS`; senão = `ONLINE`.
      Não há como diferenciar `DYING_GASP`/`POWER_OFF` só com dados do ACS
      (isso normalmente vem de um trap SNMP da OLT, não do TR-069 da ONT).
    - GenieACS não documenta uma chave de idempotência nativa para tasks.
      Mitigação: antes de enfileirar um reboot, checa se já existe uma task
      `reboot` pendente para o device e reaproveita em vez de duplicar.
    """

    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        username: str = "",
        password: str = "",
        rx_power_los_threshold_dbm: float = -28.0,
    ) -> None:
        if not base_url:
            raise GenieACSNotConfiguredError(
                "GENIEACS_BASE_URL não configurado. Ver docs/connectors/genieacs.md."
            )
        self._base_url = base_url.rstrip("/")
        auth = (username, password) if username else None
        self._client = client or httpx.AsyncClient(auth=auth)
        self._breaker = CircuitBreaker()
        self._rx_los_threshold = rx_power_los_threshold_dbm

    async def _get(self, path: str, params: dict | None = None) -> list | dict:
        async def _do_call() -> httpx.Response:
            return await self._client.get(f"{self._base_url}{path}", params=params)

        response = await call_with_resilience(_do_call, breaker=self._breaker, max_retries=2)
        if response.status_code >= 400:
            raise ConnectorError(f"GenieACS GET {path}: HTTP {response.status_code} — {response.text[:300]}")
        return response.json()

    async def _find_device(self, cpe_serial: str) -> dict | None:
        query = json.dumps({"_deviceId._SerialNumber": cpe_serial})
        devices = await self._get("/devices/", params={"query": query})
        return devices[0] if devices else None

    @staticmethod
    def _param(device: dict, path: str) -> object | None:
        node: object = device
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        if isinstance(node, dict) and "_value" in node:
            return node["_value"]
        return None

    def _infer_status(self, rx_power_dbm: float | None) -> ONUStatus:
        if rx_power_dbm is None:
            return ONUStatus.UNKNOWN
        if rx_power_dbm <= self._rx_los_threshold:
            return ONUStatus.LOS
        return ONUStatus.ONLINE

    async def get_cpe_diagnostics(self, cpe_serial: str) -> CPEDiagnostics:
        device = await self._find_device(cpe_serial)
        if device is None:
            raise ConnectorError(f"CPE {cpe_serial} não encontrado no GenieACS")

        rx_power: float | None = None
        for path in _RX_POWER_PATHS:
            raw = self._param(device, path)
            if raw is not None:
                try:
                    rx_power = float(raw)
                except (TypeError, ValueError):
                    rx_power = None
                break

        wifi_channel = self._param(device, _WIFI_CHANNEL_PATH)
        wifi_clients = self._param(device, _WIFI_CLIENTS_PATH)

        last_inform_ms = device.get("_lastInform")
        last_seen = (
            datetime.fromtimestamp(last_inform_ms / 1000, tz=UTC)
            if isinstance(last_inform_ms, int | float)
            else None
        )

        return CPEDiagnostics(
            cpe_serial=cpe_serial,
            onu_status=self._infer_status(rx_power),
            rx_power_dbm=rx_power,
            wifi_channel=int(wifi_channel) if isinstance(wifi_channel, int | float) else None,
            wifi_client_count=int(wifi_clients) if isinstance(wifi_clients, int | float) else None,
            last_seen=last_seen,
        )

    async def reboot_cpe(self, cpe_serial: str, idempotency_key: str) -> ActionResult:
        device = await self._find_device(cpe_serial)
        if device is None:
            return ActionResult(
                success=False, idempotency_key=idempotency_key, message=f"CPE {cpe_serial} não encontrado"
            )
        device_id = device["_id"]

        # Mitigação de idempotência (spec §5) — GenieACS não tem chave nativa
        # para tasks, então checamos se já existe um reboot pendente antes
        # de enfileirar outro.
        pending = await self._get(
            "/tasks/", params={"query": json.dumps({"device": device_id, "name": "reboot"})}
        )
        if pending:
            return ActionResult(
                success=True, idempotency_key=idempotency_key, message="reboot já estava enfileirado para este CPE"
            )

        async def _do_call() -> httpx.Response:
            return await self._client.post(
                f"{self._base_url}/devices/{quote(device_id, safe='')}/tasks",
                params={"connection_request": ""},
                json={"name": "reboot"},
            )

        # max_retries=0: nunca reenvia um reboot automaticamente (spec §5).
        response = await call_with_resilience(_do_call, breaker=self._breaker, max_retries=0)
        if response.status_code not in (200, 202):
            raise ConnectorError(
                f"GenieACS reboot de {cpe_serial} falhou: HTTP {response.status_code} — {response.text[:300]}"
            )
        queued_only = response.status_code == 202
        return ActionResult(
            success=True,
            idempotency_key=idempotency_key,
            message=(
                "reboot enfileirado — CPE offline, será executado no próximo inform"
                if queued_only
                else "reboot executado"
            ),
        )


def get_genieacs_adapter(settings) -> GenieACSAdapter | None:
    """Fábrica no mesmo estilo de `get_connector`/`get_llm_client`. `None`
    quando `ACS_PROVIDER != 'genieacs'` — nenhum adapter é opt-in."""
    if settings.acs_provider != "genieacs":
        return None
    return GenieACSAdapter(
        base_url=settings.genieacs_base_url,
        username=settings.genieacs_username,
        password=settings.genieacs_password,
        rx_power_los_threshold_dbm=settings.genieacs_rx_power_los_threshold_dbm,
    )
