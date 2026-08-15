"""ConnectorHub — compõe ERP + ACS + NMS atrás de uma única `ISPConnector`.

Espelha literalmente o desenho da spec §4.4: "ISP CONNECTOR HUB (o
diferencial competitivo) — ERP | ACS | AAA | NMS | Notificação" atrás de
um contrato único, para o `CallOrchestrator`/`ToolExecutor` nunca
precisarem saber de qual sistema um dado realmente veio.

`get_cpe_diagnostics`/`reboot_cpe` usam o adapter de ACS quando presente,
senão caem no que o conector de ERP já faz (`MockISPConnector` funciona
de verdade; `HubsoftConnector` sozinho levanta `NotImplementedError` —
confirmado que esses dados não existem no ERP, spec §4.4). Mesmo padrão
para `get_area_incidents` com o adapter de NMS.
"""
from __future__ import annotations

from voxisp.connectors.base import ISPConnector
from voxisp.connectors.genieacs import GenieACSAdapter
from voxisp.connectors.models import (
    ActionResult,
    ConnectionStatus,
    CPEDiagnostics,
    Incident,
    Invoice,
    PaymentPayload,
    ServiceOrder,
    SODraft,
    Subscriber,
    UnlockResult,
)
from voxisp.connectors.models import (
    Protocol as ProtocolRecord,
)
from voxisp.connectors.zabbix import ZabbixAdapter


class ConnectorHub(ISPConnector):
    def __init__(
        self,
        erp: ISPConnector,
        acs: GenieACSAdapter | None = None,
        nms: ZabbixAdapter | None = None,
    ) -> None:
        self._erp = erp
        self._acs = acs
        self._nms = nms

    # -- Sempre delegado ao ERP --------------------------------------------

    async def find_subscriber(self, cpf: str | None = None, phone: str | None = None) -> Subscriber | None:
        return await self._erp.find_subscriber(cpf, phone)

    async def get_invoices(self, subscriber_id: str, status: str) -> list[Invoice]:
        return await self._erp.get_invoices(subscriber_id, status)

    async def issue_second_copy(self, invoice_id: str) -> PaymentPayload:
        return await self._erp.issue_second_copy(invoice_id)

    async def request_trust_unlock(self, subscriber_id: str) -> UnlockResult:
        return await self._erp.request_trust_unlock(subscriber_id)

    async def get_connection_status(self, subscriber_id: str) -> ConnectionStatus:
        return await self._erp.get_connection_status(subscriber_id)

    async def list_service_orders(self, subscriber_id: str) -> list[ServiceOrder]:
        return await self._erp.list_service_orders(subscriber_id)

    async def create_service_order(self, payload: SODraft) -> ServiceOrder:
        return await self._erp.create_service_order(payload)

    async def create_protocol(self, subscriber_id: str, summary: str) -> ProtocolRecord:
        return await self._erp.create_protocol(subscriber_id, summary)

    # -- ACS (GenieACS) quando configurado, senão cai no ERP ---------------

    async def get_cpe_diagnostics(self, cpe_serial: str) -> CPEDiagnostics:
        if self._acs is not None:
            return await self._acs.get_cpe_diagnostics(cpe_serial)
        return await self._erp.get_cpe_diagnostics(cpe_serial)

    async def reboot_cpe(self, cpe_serial: str, idempotency_key: str) -> ActionResult:
        if self._acs is not None:
            return await self._acs.reboot_cpe(cpe_serial, idempotency_key)
        return await self._erp.reboot_cpe(cpe_serial, idempotency_key)

    # -- NMS (Zabbix) quando configurado, senão cai no ERP ------------------

    async def get_area_incidents(self, olt_id: str, pon: str) -> list[Incident]:
        if self._nms is not None:
            return await self._nms.get_area_incidents(olt_id, pon)
        return await self._erp.get_area_incidents(olt_id, pon)
