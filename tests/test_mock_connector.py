"""Testes de contrato do conector mock — mesma bateria que qualquer
conector real (Hubsoft, IXC, SGP, Voalle) deve passar (spec §4.4)."""
from datetime import UTC, datetime

import pytest

from voxisp.connectors.base import ConnectorError, ISPConnector
from voxisp.connectors.mock import MockISPConnector
from voxisp.connectors.models import (
    InvoiceStatus,
    ServiceOrderStatus,
    SODraft,
    VisitAction,
    VisitDraft,
)


def test_mock_implements_protocol():
    assert isinstance(MockISPConnector(), ISPConnector)


async def test_find_subscriber_by_cpf():
    connector = MockISPConnector()
    sub = await connector.find_subscriber(cpf="111.444.777-35")
    assert sub is not None
    assert sub.id == "sub-001"


async def test_find_subscriber_not_found():
    connector = MockISPConnector()
    sub = await connector.find_subscriber(cpf="00000000000")
    assert sub is None


async def test_issue_second_copy_returns_pix_and_digitable_line():
    connector = MockISPConnector()
    invoices = await connector.get_invoices("sub-001", status="open")
    assert len(invoices) == 1
    assert invoices[0].status == InvoiceStatus.OPEN

    payload = await connector.issue_second_copy(invoices[0].id)
    assert payload.pix_copy_paste
    assert payload.digitable_line


async def test_trust_unlock_ineligible_without_open_invoice():
    connector = MockISPConnector()
    result = await connector.request_trust_unlock("sub-002")
    assert result.eligible is False


async def test_reboot_cpe_is_idempotent_by_key():
    connector = MockISPConnector()
    result = await connector.reboot_cpe("ONU-AABBCC001", idempotency_key="idem-123")
    assert result.success is True
    assert result.idempotency_key == "idem-123"


async def test_create_protocol_has_number():
    connector = MockISPConnector()
    protocol = await connector.create_protocol("sub-001", "teste")
    assert protocol.protocol_number


async def test_manage_visit_schedule_marks_order_scheduled():
    connector = MockISPConnector()
    so = await connector.create_service_order(
        SODraft(subscriber_id="sub-001", category="visita_tecnica", summary="teste")
    )

    updated = await connector.manage_visit(
        VisitDraft(subscriber_id="sub-001", action=VisitAction.SCHEDULE, service_order_id=so.id)
    )

    assert updated.status == ServiceOrderStatus.SCHEDULED


async def test_manage_visit_reschedule_updates_window():
    connector = MockISPConnector()
    so = await connector.create_service_order(
        SODraft(subscriber_id="sub-001", category="visita_tecnica", summary="teste")
    )

    updated = await connector.manage_visit(
        VisitDraft(
            subscriber_id="sub-001",
            action=VisitAction.RESCHEDULE,
            service_order_id=so.id,
            window_start=datetime(2026, 9, 1, 14, 0, tzinfo=UTC),
        )
    )

    assert updated.status == ServiceOrderStatus.SCHEDULED
    assert updated.scheduled_window is not None
    assert "01/09" in updated.scheduled_window


async def test_manage_visit_cancel_marks_order_cancelled():
    connector = MockISPConnector()
    so = await connector.create_service_order(
        SODraft(subscriber_id="sub-001", category="visita_tecnica", summary="teste")
    )

    updated = await connector.manage_visit(
        VisitDraft(subscriber_id="sub-001", action=VisitAction.CANCEL, service_order_id=so.id)
    )

    assert updated.status == ServiceOrderStatus.CANCELLED


async def test_manage_visit_unknown_order_raises():
    connector = MockISPConnector()
    with pytest.raises(ConnectorError):
        await connector.manage_visit(
            VisitDraft(subscriber_id="sub-001", action=VisitAction.SCHEDULE, service_order_id="os-inexistente")
        )
