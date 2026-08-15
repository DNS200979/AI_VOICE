"""Testes do algoritmo de correlação de massivo — spec §4.5."""
from voxisp.connectors.base import ConnectorError
from voxisp.connectors.hub import ConnectorHub
from voxisp.connectors.mock import MockISPConnector
from voxisp.massive_detection import check_massive_incident


async def test_subscriber_in_massive_pon_is_flagged():
    connector = MockISPConnector()
    sub = await connector.find_subscriber(cpf="11144477735")  # sub-001, PON-04 com 7 ONUs em LOS
    assert sub is not None

    result = await check_massive_incident(connector, sub)

    assert result.is_massive is True
    assert result.incident is not None
    assert result.incident.affected_count == 7
    assert result.eta_message is not None


async def test_subscriber_without_incident_is_not_flagged():
    connector = MockISPConnector()
    sub = await connector.find_subscriber(cpf="52998224725")  # sub-002, PON sem incidente
    assert sub is not None

    result = await check_massive_incident(connector, sub)

    assert result.is_massive is False
    assert result.incident is None


async def test_threshold_respected():
    connector = MockISPConnector()
    sub = await connector.find_subscriber(cpf="11144477735")
    assert sub is not None

    # Com um limiar mais alto que o nº de ONUs afetadas, não deve classificar como massivo.
    result = await check_massive_incident(connector, sub, los_threshold=10)
    assert result.is_massive is False


class _NMSNotConfiguredERP(MockISPConnector):
    """Simula um ERP puro (Hubsoft real) sem adapter de NMS — get_area_incidents
    não existe, exatamente como o HubsoftConnector real se comporta."""

    async def get_area_incidents(self, olt_id: str, pon: str):
        raise NotImplementedError("get_area_incidents não existe neste ERP")


async def test_degrades_gracefully_when_nms_not_configured():
    """Spec §4.4, degradação graciosa: sem adapter de NMS (ex.: HubsoftConnector
    puro, sem ZabbixAdapter), a chamada não pode derrubar o diagnóstico —
    só segue para o caminho individual."""
    connector = ConnectorHub(_NMSNotConfiguredERP())
    sub = await connector.find_subscriber(cpf="11144477735")
    assert sub is not None

    result = await check_massive_incident(connector, sub)

    assert result.is_massive is False
    assert result.incident is None


class _NMSDownERP(MockISPConnector):
    async def get_area_incidents(self, olt_id: str, pon: str):
        raise ConnectorError("Zabbix fora do ar (simulado)")


async def test_degrades_gracefully_when_nms_call_fails():
    connector = ConnectorHub(_NMSDownERP())
    sub = await connector.find_subscriber(cpf="11144477735")
    assert sub is not None

    result = await check_massive_incident(connector, sub)

    assert result.is_massive is False
