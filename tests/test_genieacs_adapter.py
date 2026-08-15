"""Testes do GenieACSAdapter — sem rede: usa `httpx.MockTransport`
simulando o NBI do GenieACS (endpoints verificados em
docs.genieacs.com/en/stable/api-reference.html).
"""
import json as _json

import httpx
import pytest

from voxisp.config import Settings
from voxisp.connectors.base import ConnectorError
from voxisp.connectors.genieacs import (
    GenieACSAdapter,
    GenieACSNotConfiguredError,
    get_genieacs_adapter,
)
from voxisp.connectors.models import ONUStatus


def _device(rx_power: float | None = -18.5, wifi_channel=6, wifi_clients=4, last_inform_ms=1700000000000):
    doc: dict = {
        "_id": "202BC1-ONU123-ABC123",
        "_deviceId": {"_SerialNumber": "ABC123"},
        "InternetGatewayDevice": {
            "LANDevice": {
                "1": {
                    "WLANConfiguration": {
                        "1": {
                            "Channel": {"_value": wifi_channel},
                            "TotalAssociations": {"_value": wifi_clients},
                        }
                    }
                }
            },
        },
    }
    if last_inform_ms is not None:
        doc["_lastInform"] = last_inform_ms
    if rx_power is not None:
        # Caminho real do Huawei — confirmado no fórum oficial (o typo
        # "Interafce" é do próprio fabricante).
        doc["InternetGatewayDevice"]["WANDevice"] = {
            "1": {"X_GponInterafceConfig": {"RXPower": {"_value": rx_power}}}
        }
    return doc


def _adapter_with(handler) -> GenieACSAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return GenieACSAdapter("https://acs.example.com", client=client)


def test_requires_base_url():
    with pytest.raises(GenieACSNotConfiguredError):
        GenieACSAdapter("")


async def test_get_cpe_diagnostics_parses_huawei_rx_power_path():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/devices/"
        return httpx.Response(200, json=[_device(rx_power=-18.5)])

    adapter = _adapter_with(handler)
    diag = await adapter.get_cpe_diagnostics("ABC123")

    assert diag.onu_status == ONUStatus.ONLINE
    assert diag.rx_power_dbm == -18.5
    assert diag.wifi_channel == 6
    assert diag.wifi_client_count == 4
    assert diag.last_seen is not None


async def test_get_cpe_diagnostics_los_below_threshold():
    adapter = _adapter_with(lambda r: httpx.Response(200, json=[_device(rx_power=-32.0)]))
    diag = await adapter.get_cpe_diagnostics("ABC123")
    assert diag.onu_status == ONUStatus.LOS


async def test_get_cpe_diagnostics_unknown_when_no_rx_power_found():
    """Nenhum dos caminhos de fabricante conhecidos bateu — não inventa um
    status, admite que não sabe (spec §12: nunca alucinar dado técnico)."""
    adapter = _adapter_with(lambda r: httpx.Response(200, json=[_device(rx_power=None)]))
    diag = await adapter.get_cpe_diagnostics("ABC123")
    assert diag.onu_status == ONUStatus.UNKNOWN
    assert diag.rx_power_dbm is None


async def test_get_cpe_diagnostics_raises_when_not_found():
    adapter = _adapter_with(lambda r: httpx.Response(200, json=[]))
    with pytest.raises(ConnectorError, match="não encontrado"):
        await adapter.get_cpe_diagnostics("NOPE")


async def test_reboot_cpe_queues_task_when_none_pending():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/devices/":
            return httpx.Response(200, json=[_device()])
        if request.url.path == "/tasks/":
            return httpx.Response(200, json=[])
        assert request.url.path == "/devices/202BC1-ONU123-ABC123/tasks"
        assert _json.loads(request.read()) == {"name": "reboot"}
        return httpx.Response(200, json={"_id": "task1"})

    adapter = _adapter_with(handler)
    result = await adapter.reboot_cpe("ABC123", idempotency_key="idem-1")

    assert result.success is True
    assert "executado" in result.message


async def test_reboot_cpe_202_means_queued_for_next_inform():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/devices/":
            return httpx.Response(200, json=[_device()])
        if request.url.path == "/tasks/":
            return httpx.Response(200, json=[])
        return httpx.Response(202, json={"_id": "task1"})

    adapter = _adapter_with(handler)
    result = await adapter.reboot_cpe("ABC123", idempotency_key="idem-1")

    assert result.success is True
    assert "offline" in result.message.lower()


async def test_reboot_cpe_reuses_pending_task_instead_of_duplicating():
    """Mitigação de idempotência (spec §5) — GenieACS não tem chave nativa."""
    post_task_calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/devices/":
            return httpx.Response(200, json=[_device()])
        if request.url.path == "/tasks/":
            return httpx.Response(200, json=[{"_id": "existing-task", "name": "reboot"}])
        post_task_calls["count"] += 1
        return httpx.Response(200, json={"_id": "should-not-happen"})

    adapter = _adapter_with(handler)
    result = await adapter.reboot_cpe("ABC123", idempotency_key="idem-1")

    assert result.success is True
    assert "já estava enfileirado" in result.message
    assert post_task_calls["count"] == 0


async def test_reboot_cpe_device_not_found():
    adapter = _adapter_with(lambda r: httpx.Response(200, json=[]))
    result = await adapter.reboot_cpe("NOPE", idempotency_key="idem-1")

    assert result.success is False
    assert "não encontrado" in result.message


def test_get_genieacs_adapter_factory_none_when_not_configured():
    settings = Settings(acs_provider="none")
    assert get_genieacs_adapter(settings) is None


def test_get_genieacs_adapter_factory_builds_when_configured():
    settings = Settings(acs_provider="genieacs", genieacs_base_url="https://acs.example.com")
    adapter = get_genieacs_adapter(settings)
    assert isinstance(adapter, GenieACSAdapter)
