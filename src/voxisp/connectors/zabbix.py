"""ZabbixAdapter — NMS real, contra a API JSON-RPC do Zabbix.

Endpoints e formato verificados em:
- https://www.zabbix.com/documentation/current/en/manual/api
- https://www.zabbix.com/documentation/current/en/manual/api/reference/host/get
- https://www.zabbix.com/documentation/current/en/manual/api/reference/problem/get

Implementa só o que `get_area_incidents` precisa (spec §4.5, o algoritmo de
correlação de massivo) — não é um `ISPConnector` completo. Composto pelo
`ConnectorHub` (`connectors/hub.py`).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx

from voxisp.connectors.base import ConnectorError
from voxisp.connectors.models import Incident
from voxisp.connectors.resilience import CircuitBreaker, call_with_resilience


class ZabbixNotConfiguredError(Exception):
    """`ZABBIX_BASE_URL`/`USERNAME`/`PASSWORD` ausentes (com `NMS_PROVIDER=zabbix`)."""


class ZabbixAdapter:
    """Correlação de incidente de rede via Zabbix — spec §4.4/§4.5.

    Autenticação: `user.login` retorna um token que vai como
    `Authorization: Bearer <token>` nas chamadas seguintes (formato atual
    documentado; versões do Zabbix anteriores à 6.4 esperam o token no
    campo `auth` do corpo JSON-RPC em vez do header — não implementado
    aqui, ver checklist em docs/connectors/zabbix.md).

    Correlação OLT↔host: o `olt_id` do ERP (ex.: `id_equipamento` da
    Hubsoft) e o `hostid` do Zabbix são espaços de ID **diferentes**. A
    forma determinística de ligar os dois é marcar o host da OLT no
    Zabbix com uma tag cuja chave é `olt_tag_key` (padrão `"olt_id"`) e
    valor igual ao `olt_id` do ERP — é uma convenção de integração, não
    algo que a API force. Sem essa tag, cai para busca aproximada por nome
    (`host.get` com `search`), que depende de como o provedor nomeia seus
    hosts no Zabbix.
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        client: httpx.AsyncClient | None = None,
        olt_tag_key: str = "olt_id",
        confirmed_incident_affected_count: int = 3,
    ) -> None:
        if not base_url or not username or not password:
            raise ZabbixNotConfiguredError(
                "ZABBIX_BASE_URL/USERNAME/PASSWORD não configurados. Ver docs/connectors/zabbix.md."
            )
        self._rpc_url = f"{base_url.rstrip('/')}/api_jsonrpc.php"
        self._username = username
        self._password = password
        self._client = client or httpx.AsyncClient()
        self._breaker = CircuitBreaker()
        self._olt_tag_key = olt_tag_key
        # Zabbix não devolve "quantas ONUs afetadas" — um problema ativo no
        # host da OLT já satisfaz a regra do §4.5 ("OU Zabbix tem alarme
        # ativo no elemento pai"), então usamos o próprio limiar padrão de
        # massivo como affected_count aproximado.
        self._confirmed_incident_affected_count = confirmed_incident_affected_count
        self._token: str | None = None

    async def _call(self, method: str, params: dict, *, authed: bool = True) -> object:
        body = {"jsonrpc": "2.0", "method": method, "params": params, "id": str(uuid.uuid4())}
        headers = {"Content-Type": "application/json-rpc"}
        if authed:
            await self._ensure_token()
            headers["Authorization"] = f"Bearer {self._token}"

        async def _do_call() -> httpx.Response:
            return await self._client.post(self._rpc_url, json=body, headers=headers)

        response = await call_with_resilience(_do_call, breaker=self._breaker, max_retries=2)
        if response.status_code >= 400:
            raise ConnectorError(f"Zabbix {method}: HTTP {response.status_code} — {response.text[:300]}")
        data = response.json()
        if "error" in data:
            raise ConnectorError(f"Zabbix {method}: {data['error']}")
        return data["result"]

    async def _ensure_token(self) -> None:
        if self._token is not None:
            return
        self._token = await self._call(
            "user.login", {"username": self._username, "password": self._password}, authed=False
        )

    async def _find_host_id(self, olt_id: str) -> str | None:
        by_tag = await self._call(
            "host.get",
            {"output": ["hostid", "host"], "tags": [{"tag": self._olt_tag_key, "value": olt_id}]},
        )
        if by_tag:
            return by_tag[0]["hostid"]

        # Fallback aproximado — ver docstring da classe.
        by_name = await self._call("host.get", {"output": ["hostid", "host"], "search": {"host": olt_id}})
        return by_name[0]["hostid"] if by_name else None

    async def get_area_incidents(self, olt_id: str, pon: str) -> list[Incident]:
        host_id = await self._find_host_id(olt_id)
        if host_id is None:
            return []

        problems = await self._call(
            "problem.get",
            {"hostids": [host_id], "recent": False, "sortfield": "eventid", "sortorder": "DESC"},
        )
        return [
            Incident(
                id=str(problem["eventid"]),
                olt_id=olt_id,
                pon=pon,
                affected_count=self._confirmed_incident_affected_count,
                started_at=datetime.fromtimestamp(int(problem["clock"]), tz=UTC),
                description=problem.get("name"),
            )
            for problem in problems
        ]


def get_zabbix_adapter(settings) -> ZabbixAdapter | None:
    """Fábrica no mesmo estilo de `get_connector`/`get_llm_client`. `None`
    quando `NMS_PROVIDER != 'zabbix'` — nenhum adapter é opt-in."""
    if settings.nms_provider != "zabbix":
        return None
    return ZabbixAdapter(
        base_url=settings.zabbix_base_url,
        username=settings.zabbix_username,
        password=settings.zabbix_password,
        olt_tag_key=settings.zabbix_olt_tag_key,
        confirmed_incident_affected_count=settings.massive_los_threshold,
    )
