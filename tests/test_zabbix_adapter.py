"""Testes do ZabbixAdapter — sem rede: usa `httpx.MockTransport` simulando
a API JSON-RPC do Zabbix (endpoints verificados na doc oficial).
"""
import json as _json

import httpx
import pytest

from voxisp.config import Settings
from voxisp.connectors.base import ConnectorError
from voxisp.connectors.zabbix import ZabbixAdapter, ZabbixNotConfiguredError, get_zabbix_adapter


def _rpc_handler(routes: dict):
    """`routes`: nome do método -> `callable(params) -> result`. `user.login`
    é tratado automaticamente (token fixo `"fake-token"`)."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json.loads(request.read())
        method = body["method"]
        if method == "user.login":
            return httpx.Response(200, json={"jsonrpc": "2.0", "result": "fake-token", "id": body["id"]})
        assert request.headers["authorization"] == "Bearer fake-token"
        result = routes[method](body["params"])
        return httpx.Response(200, json={"jsonrpc": "2.0", "result": result, "id": body["id"]})

    return handler


def _adapter_with(routes: dict) -> ZabbixAdapter:
    transport = httpx.MockTransport(_rpc_handler(routes))
    client = httpx.AsyncClient(transport=transport)
    return ZabbixAdapter("https://zabbix.example.com", "Admin", "zabbix", client=client)


def test_requires_credentials():
    with pytest.raises(ZabbixNotConfiguredError):
        ZabbixAdapter("", "", "")


async def test_get_area_incidents_via_tag_correlation():
    def host_get(params):
        assert params["tags"] == [{"tag": "olt_id", "value": "1410"}]
        return [{"hostid": "555", "host": "OLT BDCOM"}]

    def problem_get(params):
        assert params["hostids"] == ["555"]
        return [{"eventid": "999", "clock": "1700000000", "name": "Interface down"}]

    adapter = _adapter_with({"host.get": host_get, "problem.get": problem_get})
    incidents = await adapter.get_area_incidents("1410", "PON5")

    assert len(incidents) == 1
    assert incidents[0].id == "999"
    assert incidents[0].olt_id == "1410"
    assert incidents[0].pon == "PON5"
    assert incidents[0].description == "Interface down"
    assert incidents[0].affected_count == 3  # default: massive_los_threshold


async def test_get_area_incidents_falls_back_to_name_search_without_tag():
    calls = {"host.get": 0}

    def host_get(params):
        calls["host.get"] += 1
        if "tags" in params:
            return []  # provedor não marcou a tag olt_id neste host
        assert params["search"] == {"host": "OLT BDCOM"}
        return [{"hostid": "555", "host": "OLT BDCOM"}]

    adapter = _adapter_with({"host.get": host_get, "problem.get": lambda p: []})
    incidents = await adapter.get_area_incidents("OLT BDCOM", "PON5")

    assert incidents == []
    assert calls["host.get"] == 2  # tentou por tag, depois caiu para nome


async def test_get_area_incidents_returns_empty_when_host_not_found():
    adapter = _adapter_with({"host.get": lambda p: []})
    incidents = await adapter.get_area_incidents("olt-desconhecida", "PON5")
    assert incidents == []


async def test_zabbix_error_response_raises_connector_error():
    def handler(request: httpx.Request) -> httpx.Response:
        body = _json.loads(request.read())
        if body["method"] == "user.login":
            return httpx.Response(200, json={"jsonrpc": "2.0", "result": "fake-token", "id": body["id"]})
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "error": {"code": -32602, "message": "Invalid params"}, "id": body["id"]},
        )

    transport = httpx.MockTransport(handler)
    adapter = ZabbixAdapter(
        "https://zabbix.example.com", "Admin", "zabbix", client=httpx.AsyncClient(transport=transport)
    )

    with pytest.raises(ConnectorError):
        await adapter.get_area_incidents("1410", "PON5")


def test_get_zabbix_adapter_factory_none_when_not_configured():
    settings = Settings(nms_provider="none")
    assert get_zabbix_adapter(settings) is None


def test_get_zabbix_adapter_factory_builds_when_configured():
    settings = Settings(
        nms_provider="zabbix",
        zabbix_base_url="https://zabbix.example.com",
        zabbix_username="Admin",
        zabbix_password="zabbix",
    )
    adapter = get_zabbix_adapter(settings)
    assert isinstance(adapter, ZabbixAdapter)
