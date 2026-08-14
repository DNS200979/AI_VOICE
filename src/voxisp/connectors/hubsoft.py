"""Conector Hubsoft — STUB aguardando documentação da API.

Ver `docs/connectors/hubsoft.md` para o checklist completo do que falta:
endpoints reais, schema de autenticação, mapeamento de campos.

Cada método já está com a assinatura final do `ISPConnector`, plugado na
fábrica (`get_connector("hubsoft")`) e preparado para usar a camada de
resiliência comum (`resilience.py` — timeout, retry, circuit breaker,
spec §4.4). O corpo levanta `HubsoftNotConfiguredError`/`NotImplementedError`
até termos a doc real: nenhuma chamada HTTP é feita hoje.
"""
from __future__ import annotations

from voxisp.config import Settings
from voxisp.connectors.base import ISPConnector
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
from voxisp.connectors.resilience import CircuitBreaker

_NOT_IMPLEMENTED = (
    "HubsoftConnector.{method}: aguardando documentação da API Hubsoft. "
    "Ver docs/connectors/hubsoft.md para o checklist e o endpoint hipotético."
)


class HubsoftNotConfiguredError(Exception):
    """`HUBSOFT_BASE_URL`/`HUBSOFT_CLIENT_ID`/`HUBSOFT_CLIENT_SECRET` ausentes."""


class HubsoftConnector(ISPConnector):
    """Implementação real (stubada) do `ISPConnector` para o ERP Hubsoft.

    Um circuit breaker por instância — quando a chamada HTTP real existir,
    cada método deve envolver a request em
    `call_with_resilience(lambda: ..., breaker=self._breaker)`.
    """

    def __init__(self, settings: Settings) -> None:
        if not settings.hubsoft_base_url:
            raise HubsoftNotConfiguredError(
                "HUBSOFT_BASE_URL não configurado. Preencha o .env conforme "
                "docs/connectors/hubsoft.md antes de usar ISP_CONNECTOR=hubsoft."
            )
        self._settings = settings
        self._breaker = CircuitBreaker()
        self._access_token: str | None = None  # preenchido por _authenticate() quando existir

    async def _authenticate(self) -> str:
        """TODO(hubsoft-docs): fluxo OAuth2 client_credentials assumido — confirmar."""
        raise NotImplementedError(_NOT_IMPLEMENTED.format(method="_authenticate"))

    async def find_subscriber(
        self, cpf: str | None = None, phone: str | None = None
    ) -> Subscriber | None:
        raise NotImplementedError(_NOT_IMPLEMENTED.format(method="find_subscriber"))

    async def get_invoices(self, subscriber_id: str, status: str) -> list[Invoice]:
        raise NotImplementedError(_NOT_IMPLEMENTED.format(method="get_invoices"))

    async def issue_second_copy(self, invoice_id: str) -> PaymentPayload:
        raise NotImplementedError(_NOT_IMPLEMENTED.format(method="issue_second_copy"))

    async def request_trust_unlock(self, subscriber_id: str) -> UnlockResult:
        raise NotImplementedError(_NOT_IMPLEMENTED.format(method="request_trust_unlock"))

    async def get_connection_status(self, subscriber_id: str) -> ConnectionStatus:
        raise NotImplementedError(_NOT_IMPLEMENTED.format(method="get_connection_status"))

    async def get_cpe_diagnostics(self, cpe_serial: str) -> CPEDiagnostics:
        raise NotImplementedError(_NOT_IMPLEMENTED.format(method="get_cpe_diagnostics"))

    async def reboot_cpe(self, cpe_serial: str, idempotency_key: str) -> ActionResult:
        raise NotImplementedError(_NOT_IMPLEMENTED.format(method="reboot_cpe"))

    async def list_service_orders(self, subscriber_id: str) -> list[ServiceOrder]:
        raise NotImplementedError(_NOT_IMPLEMENTED.format(method="list_service_orders"))

    async def create_service_order(self, payload: SODraft) -> ServiceOrder:
        raise NotImplementedError(_NOT_IMPLEMENTED.format(method="create_service_order"))

    async def get_area_incidents(self, olt_id: str, pon: str) -> list[Incident]:
        raise NotImplementedError(_NOT_IMPLEMENTED.format(method="get_area_incidents"))

    async def create_protocol(self, subscriber_id: str, summary: str) -> ProtocolRecord:
        raise NotImplementedError(_NOT_IMPLEMENTED.format(method="create_protocol"))
