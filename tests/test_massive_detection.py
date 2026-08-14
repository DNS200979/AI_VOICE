"""Testes do algoritmo de correlação de massivo — spec §4.5."""
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
