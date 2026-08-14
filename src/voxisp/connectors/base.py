"""Interface abstrata do ISP Connector Hub (spec §4.4).

Cada ERP/ACS suportado (Hubsoft, IXC, SGP, Voalle, MK Solutions...)
implementa este `Protocol`. É o que permite vender o mesmo produto de
voicebot para provedores com sistemas de retaguarda diferentes sem
tocar na FSM/orquestrador.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

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


@runtime_checkable
class ISPConnector(Protocol):
    """Contrato único que todo conector de ERP/ACS/AAA deve implementar."""

    async def find_subscriber(
        self, cpf: str | None = None, phone: str | None = None
    ) -> Subscriber | None: ...

    async def get_invoices(self, subscriber_id: str, status: str) -> list[Invoice]: ...

    async def issue_second_copy(self, invoice_id: str) -> PaymentPayload: ...

    async def request_trust_unlock(self, subscriber_id: str) -> UnlockResult: ...

    async def get_connection_status(self, subscriber_id: str) -> ConnectionStatus: ...

    async def get_cpe_diagnostics(self, cpe_serial: str) -> CPEDiagnostics: ...

    async def reboot_cpe(self, cpe_serial: str, idempotency_key: str) -> ActionResult: ...

    async def list_service_orders(self, subscriber_id: str) -> list[ServiceOrder]: ...

    async def create_service_order(self, payload: SODraft) -> ServiceOrder: ...

    async def get_area_incidents(self, olt_id: str, pon: str) -> list[Incident]: ...

    async def create_protocol(self, subscriber_id: str, summary: str) -> ProtocolRecord: ...


class ConnectorError(Exception):
    """Erro genérico de conector (rede, timeout, resposta inválida)."""


class SubscriberNotFoundError(ConnectorError):
    pass
