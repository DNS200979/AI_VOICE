"""Testes do ConnectorHub — compõe ERP + ACS + NMS atrás de um único
`ISPConnector` (spec §4.4). Sem adapters, é um passthrough transparente
para o ERP; com adapters, delega só os 3 métodos de telemetria.
"""
from datetime import UTC, datetime

from voxisp.connectors.base import ISPConnector
from voxisp.connectors.hub import ConnectorHub
from voxisp.connectors.mock import MockISPConnector
from voxisp.connectors.models import ActionResult, CPEDiagnostics, Incident, ONUStatus


class _FakeACS:
    def __init__(self):
        self.calls: list[tuple] = []

    async def get_cpe_diagnostics(self, cpe_serial: str) -> CPEDiagnostics:
        self.calls.append(("get_cpe_diagnostics", cpe_serial))
        return CPEDiagnostics(cpe_serial=cpe_serial, onu_status=ONUStatus.ONLINE)

    async def reboot_cpe(self, cpe_serial: str, idempotency_key: str) -> ActionResult:
        self.calls.append(("reboot_cpe", cpe_serial, idempotency_key))
        return ActionResult(success=True, idempotency_key=idempotency_key)


class _FakeNMS:
    def __init__(self):
        self.calls: list[tuple] = []

    async def get_area_incidents(self, olt_id: str, pon: str) -> list[Incident]:
        self.calls.append(("get_area_incidents", olt_id, pon))
        return [Incident(id="1", olt_id=olt_id, pon=pon, affected_count=5, started_at=datetime.now(UTC))]


def test_hub_implements_protocol():
    assert isinstance(ConnectorHub(MockISPConnector()), ISPConnector)


async def test_hub_without_adapters_delegates_telemetry_to_erp():
    """Sem ACS/NMS configurados, o Hub tem que continuar funcionando
    exatamente como o ERP puro — MockISPConnector já implementa os 3
    métodos de telemetria de verdade."""
    erp = MockISPConnector()
    hub = ConnectorHub(erp)

    subscriber = await hub.find_subscriber(cpf="11144477735")
    assert subscriber is not None
    assert subscriber.cpe_serial is not None

    diag = await hub.get_cpe_diagnostics(subscriber.cpe_serial)
    assert diag.cpe_serial == subscriber.cpe_serial

    action = await hub.reboot_cpe(subscriber.cpe_serial, idempotency_key="idem-1")
    assert action.success is True

    incidents = await hub.get_area_incidents(subscriber.olt_id, subscriber.pon)
    assert isinstance(incidents, list)


async def test_hub_uses_acs_adapter_when_configured():
    acs = _FakeACS()
    hub = ConnectorHub(MockISPConnector(), acs=acs)

    diag = await hub.get_cpe_diagnostics("ONU-X")
    action = await hub.reboot_cpe("ONU-X", idempotency_key="idem-1")

    assert diag.onu_status == ONUStatus.ONLINE
    assert action.success is True
    assert acs.calls == [("get_cpe_diagnostics", "ONU-X"), ("reboot_cpe", "ONU-X", "idem-1")]


async def test_hub_uses_nms_adapter_when_configured():
    nms = _FakeNMS()
    hub = ConnectorHub(MockISPConnector(), nms=nms)

    incidents = await hub.get_area_incidents("OLT-1", "PON-4")

    assert len(incidents) == 1
    assert incidents[0].affected_count == 5
    assert nms.calls == [("get_area_incidents", "OLT-1", "PON-4")]


async def test_hub_erp_only_methods_always_delegate_regardless_of_adapters():
    erp = MockISPConnector()
    hub = ConnectorHub(erp, acs=_FakeACS(), nms=_FakeNMS())

    subscriber = await hub.find_subscriber(cpf="11144477735")
    invoices = await hub.get_invoices(subscriber.id, status="open")
    protocol = await hub.create_protocol(subscriber.id, "teste")

    assert subscriber.id == "sub-001"
    assert isinstance(invoices, list)
    assert protocol.protocol_number
