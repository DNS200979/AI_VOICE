"""Modelos de dados trocados com o ISP Connector Hub.

Espelham as entidades descritas na spec §4.4 (ISP Connector Hub) e §8
(modelo de dados). Mantidos como Pydantic models para validação de
schema na fronteira entre o LLM/FSM e os conectores reais de ERP/ACS/AAA.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel


class Subscriber(BaseModel):
    id: str
    name: str
    cpf_masked: str  # nunca CPF em claro (LGPD §6.3)
    phone: str
    plan_name: str
    contract_start: date
    loyalty_until: date | None = None
    address: str
    olt_id: str | None = None
    pon: str | None = None
    cto_id: str | None = None
    cpe_serial: str | None = None


class InvoiceStatus(StrEnum):
    OPEN = "open"
    PAID = "paid"
    OVERDUE = "overdue"


class Invoice(BaseModel):
    id: str
    subscriber_id: str
    amount_cents: int
    due_date: date
    status: InvoiceStatus
    barcode_digitable_line: str | None = None


class PaymentPayload(BaseModel):
    invoice_id: str
    pix_copy_paste: str
    digitable_line: str
    amount_cents: int
    due_date: date


class UnlockResult(BaseModel):
    subscriber_id: str
    eligible: bool
    unlocked_until: datetime | None = None
    reason: str | None = None  # motivo de inelegibilidade, se houver


class SessionState(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class ConnectionStatus(BaseModel):
    subscriber_id: str
    session_state: SessionState
    last_logon: datetime | None = None
    last_logoff: datetime | None = None
    disconnect_reason: str | None = None


class ONUStatus(StrEnum):
    ONLINE = "online"
    LOS = "los"                 # Loss of Signal
    DYING_GASP = "dying_gasp"
    POWER_OFF = "power_off"
    UNKNOWN = "unknown"


class CPEDiagnostics(BaseModel):
    cpe_serial: str
    onu_status: ONUStatus
    rx_power_dbm: float | None = None
    wifi_channel: int | None = None
    wifi_client_count: int | None = None
    last_seen: datetime | None = None


class ActionResult(BaseModel):
    success: bool
    idempotency_key: str
    message: str | None = None


class ServiceOrderStatus(StrEnum):
    OPEN = "open"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


class ServiceOrder(BaseModel):
    id: str
    subscriber_id: str
    category: str
    status: ServiceOrderStatus
    scheduled_window: str | None = None
    technician: str | None = None
    created_at: datetime


class SODraft(BaseModel):
    subscriber_id: str
    category: str
    summary: str
    preferred_window: str | None = None


class VisitAction(StrEnum):
    """As três ações que o spec §2.1 agrupa sob OPS-02 ("agendamento /
    reagendamento / cancelamento de visita técnica")."""

    SCHEDULE = "schedule"
    RESCHEDULE = "reschedule"
    CANCEL = "cancel"


class VisitDraft(BaseModel):
    """Payload de `ISPConnector.manage_visit` — método dedicado que
    substitui o reaproveitamento de `create_service_order` para OPS-02.

    `service_order_id` é obrigatório para as três ações: nenhuma delas cria
    uma OS do zero (isso continua sendo `create_service_order`/OPS-03) —
    confirmado contra a API real da Hubsoft, `ordem_servico/agendar`
    exige uma OS já existente (ver docs/connectors/hubsoft.md).
    `window_start`/`window_end` só se aplicam a `RESCHEDULE`; `reason` só
    a `CANCEL`.
    """

    subscriber_id: str
    action: VisitAction
    service_order_id: str
    window_start: datetime | None = None
    window_end: datetime | None = None
    reason: str | None = None


class Incident(BaseModel):
    id: str
    olt_id: str
    pon: str
    affected_count: int
    started_at: datetime
    eta_resolution: datetime | None = None
    description: str | None = None


class Protocol(BaseModel):
    protocol_number: str
    subscriber_id: str
    summary: str
    created_at: datetime
