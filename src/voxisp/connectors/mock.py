"""Conector mock — dados em memória para desenvolvimento local e testes.

Não fala com nenhum ERP real. Usado quando ISP_CONNECTOR=mock (padrão em
dev). Serve também como fixture de referência para novos conectores:
qualquer implementação real (Hubsoft, IXC, SGP, Voalle...) deve passar
pela mesma bateria de testes de contrato que este mock passa.
"""
from __future__ import annotations

import random
import string
import uuid
from datetime import UTC, date, datetime, timedelta

from voxisp.connectors.base import ConnectorError, ISPConnector, SubscriberNotFoundError
from voxisp.connectors.models import (
    ActionResult,
    ConnectionStatus,
    CPEDiagnostics,
    Incident,
    Invoice,
    InvoiceStatus,
    ONUStatus,
    PaymentPayload,
    ServiceOrder,
    ServiceOrderStatus,
    SessionState,
    SODraft,
    Subscriber,
    UnlockResult,
    VisitDraft,
)
from voxisp.connectors.models import (
    Protocol as ProtocolRecord,
)

# --- Base de assinantes de demonstração -------------------------------
_SUBSCRIBERS: dict[str, Subscriber] = {
    "sub-001": Subscriber(
        id="sub-001",
        name="João da Silva",
        cpf_masked="123.***.**9-00",
        phone="+5511999990001",
        plan_name="Fibra 500MB",
        contract_start=date(2023, 3, 10),
        loyalty_until=None,
        address="Rua das Acácias, 120 - Centro",
        olt_id="OLT-01",
        pon="PON-04",
        cto_id="CTO-118",
        cpe_serial="ONU-AABBCC001",
    ),
    "sub-002": Subscriber(
        id="sub-002",
        name="Maria Oliveira",
        cpf_masked="987.***.**1-11",
        phone="+5511999990002",
        plan_name="Fibra 300MB",
        contract_start=date(2022, 7, 1),
        loyalty_until=date(2026, 7, 1),
        address="Av. Brasil, 500 - Jardim Europa",
        olt_id="OLT-02",
        pon="PON-01",
        cto_id="CTO-045",
        cpe_serial="ONU-AABBCC002",
    ),
}

# CPFs de teste válidos (dígito verificador correto), amplamente usados
# como fixtures públicas — não pertencem a pessoas reais.
_CPF_INDEX = {"11144477735": "sub-001", "52998224725": "sub-002"}
_PHONE_INDEX = {s.phone: s.id for s in _SUBSCRIBERS.values()}

_INVOICES: dict[str, list[Invoice]] = {
    "sub-001": [
        Invoice(
            id="inv-1001",
            subscriber_id="sub-001",
            amount_cents=8990,
            due_date=datetime.now(UTC).date() + timedelta(days=5),
            status=InvoiceStatus.OPEN,
            barcode_digitable_line="34191.79001 01043.510047 91020.150008 1 96380000008990",
        )
    ],
    "sub-002": [],
}

# Simula um incidente de massivo na PON-04/OLT-01 (mesmo cenário do §7.2)
_ONU_STATE: dict[str, ONUStatus] = {"sub-001": ONUStatus.LOS, "sub-002": ONUStatus.ONLINE}
_MASSIVE_LOS_COUNT_BY_PON = {"OLT-01:PON-04": 7}


def _idem(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _protocol_number() -> str:
    today = datetime.now(UTC).strftime("%Y-%m%d")
    suffix = "".join(random.choices(string.digits, k=5))
    return f"{today}-{suffix}"


class MockISPConnector(ISPConnector):
    """Implementação de referência do contrato `ISPConnector`."""

    def __init__(self) -> None:
        # Por instância (não módulo): duas instâncias em testes diferentes
        # não podem enxergar as OS uma da outra — `manage_visit` passou a
        # depender de "existe uma OS aberta?" para decidir o que fazer, o
        # que tornaria um dict de módulo compartilhado uma fonte real de
        # flakiness entre testes (mesma classe de bug já corrigida antes
        # neste projeto para `Subscriber` mutável).
        self._service_orders: dict[str, list[ServiceOrder]] = {"sub-001": [], "sub-002": []}

    async def find_subscriber(
        self, cpf: str | None = None, phone: str | None = None
    ) -> Subscriber | None:
        sub_id = None
        if cpf:
            sub_id = _CPF_INDEX.get(cpf.replace(".", "").replace("-", ""))
        elif phone:
            sub_id = _PHONE_INDEX.get(phone)
        if sub_id is None:
            return None
        return _SUBSCRIBERS[sub_id]

    async def get_invoices(self, subscriber_id: str, status: str) -> list[Invoice]:
        if subscriber_id not in _SUBSCRIBERS:
            raise SubscriberNotFoundError(subscriber_id)
        invoices = _INVOICES.get(subscriber_id, [])
        if status == "all":
            return invoices
        return [inv for inv in invoices if inv.status.value == status]

    async def issue_second_copy(self, invoice_id: str) -> PaymentPayload:
        for invoices in _INVOICES.values():
            for inv in invoices:
                if inv.id == invoice_id:
                    return PaymentPayload(
                        invoice_id=inv.id,
                        pix_copy_paste=f"00020126580014BR.GOV.BCB.PIX{inv.id}...",
                        digitable_line=inv.barcode_digitable_line or "",
                        amount_cents=inv.amount_cents,
                        due_date=inv.due_date,
                    )
        raise ConnectorError(f"Fatura {invoice_id} não encontrada")

    async def request_trust_unlock(self, subscriber_id: str) -> UnlockResult:
        if subscriber_id not in _SUBSCRIBERS:
            raise SubscriberNotFoundError(subscriber_id)
        open_invoices = [i for i in _INVOICES.get(subscriber_id, []) if i.status == InvoiceStatus.OPEN]
        if not open_invoices:
            return UnlockResult(subscriber_id=subscriber_id, eligible=False, reason="Sem fatura em aberto")
        return UnlockResult(
            subscriber_id=subscriber_id,
            eligible=True,
            unlocked_until=datetime.now(UTC) + timedelta(hours=48),
        )

    async def get_connection_status(self, subscriber_id: str) -> ConnectionStatus:
        if subscriber_id not in _SUBSCRIBERS:
            raise SubscriberNotFoundError(subscriber_id)
        onu = _ONU_STATE.get(subscriber_id, ONUStatus.ONLINE)
        if onu == ONUStatus.ONLINE:
            return ConnectionStatus(subscriber_id=subscriber_id, session_state=SessionState.ONLINE)
        return ConnectionStatus(
            subscriber_id=subscriber_id,
            session_state=SessionState.OFFLINE,
            last_logoff=datetime.now(UTC) - timedelta(minutes=40),
            disconnect_reason="LOS",
        )

    async def get_cpe_diagnostics(self, cpe_serial: str) -> CPEDiagnostics:
        sub = next((s for s in _SUBSCRIBERS.values() if s.cpe_serial == cpe_serial), None)
        if sub is None:
            raise ConnectorError(f"CPE {cpe_serial} não encontrado")
        onu = _ONU_STATE.get(sub.id, ONUStatus.ONLINE)
        return CPEDiagnostics(
            cpe_serial=cpe_serial,
            onu_status=onu,
            rx_power_dbm=-31.2 if onu == ONUStatus.LOS else -18.5,
            wifi_channel=6,
            wifi_client_count=4,
            last_seen=datetime.now(UTC) - timedelta(minutes=40 if onu == ONUStatus.LOS else 0),
        )

    async def reboot_cpe(self, cpe_serial: str, idempotency_key: str) -> ActionResult:
        return ActionResult(success=True, idempotency_key=idempotency_key, message="Reboot enviado via TR-069")

    async def list_service_orders(self, subscriber_id: str) -> list[ServiceOrder]:
        return self._service_orders.get(subscriber_id, [])

    async def create_service_order(self, payload: SODraft) -> ServiceOrder:
        so = ServiceOrder(
            id=_idem("os"),
            subscriber_id=payload.subscriber_id,
            category=payload.category,
            status=ServiceOrderStatus.OPEN,
            scheduled_window=payload.preferred_window,
            technician=None,
            created_at=datetime.now(UTC),
        )
        self._service_orders.setdefault(payload.subscriber_id, []).append(so)
        return so

    async def manage_visit(self, draft: VisitDraft) -> ServiceOrder:
        orders = self._service_orders.get(draft.subscriber_id, [])
        for i, so in enumerate(orders):
            if so.id != draft.service_order_id:
                continue
            if draft.action.value == "cancel":
                updated = so.model_copy(update={"status": ServiceOrderStatus.CANCELLED})
            elif draft.action.value == "reschedule":
                window = f"{draft.window_start:%d/%m %H:%M}" if draft.window_start else so.scheduled_window
                updated = so.model_copy(
                    update={"status": ServiceOrderStatus.SCHEDULED, "scheduled_window": window}
                )
            else:  # schedule
                updated = so.model_copy(update={"status": ServiceOrderStatus.SCHEDULED})
            orders[i] = updated
            return updated
        raise ConnectorError(
            f"ordem de serviço {draft.service_order_id} não encontrada para {draft.subscriber_id}"
        )

    async def get_area_incidents(self, olt_id: str, pon: str) -> list[Incident]:
        key = f"{olt_id}:{pon}"
        count = _MASSIVE_LOS_COUNT_BY_PON.get(key, 0)
        if count == 0:
            return []
        return [
            Incident(
                id=_idem("inc"),
                olt_id=olt_id,
                pon=pon,
                affected_count=count,
                started_at=datetime.now(UTC) - timedelta(hours=2, minutes=50),
                eta_resolution=datetime.now(UTC) + timedelta(hours=2),
                description="Rompimento de fibra no troncal",
            )
        ]

    async def create_protocol(self, subscriber_id: str, summary: str) -> ProtocolRecord:
        return ProtocolRecord(
            protocol_number=_protocol_number(),
            subscriber_id=subscriber_id,
            summary=summary,
            created_at=datetime.now(UTC),
        )
