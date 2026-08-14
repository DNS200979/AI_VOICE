"""Testes das regras invioláveis da FSM — spec §7.1.

Nenhum deploy deve subir sem estas asserções passando (spec §9: "Nenhum
deploy sem passar 100% nas asserções de segurança")."""
import pytest

from voxisp.fsm.engine import CallFSM, EscalationRequired
from voxisp.fsm.states import CallState, Intent


def test_cancellation_always_escalates():
    fsm = CallFSM()
    with pytest.raises(EscalationRequired) as exc_info:
        fsm.route_intent(Intent.ESC_01_CANCELLATION)
    assert exc_info.value.code == Intent.ESC_01_CANCELLATION.value
    assert fsm.state == CallState.ESCALATED


def test_human_request_escalates_from_any_state():
    fsm = CallFSM(state=CallState.SLOT_COLLECTION)
    with pytest.raises(EscalationRequired):
        fsm.request_human()
    assert fsm.state == CallState.ESCALATED


def test_two_recognition_failures_escalate():
    fsm = CallFSM()
    fsm.record_recognition_failure("cpf")  # 1ª falha: não escalona ainda
    assert fsm.state != CallState.ESCALATED
    with pytest.raises(EscalationRequired) as exc_info:
        fsm.record_recognition_failure("cpf")  # 2ª falha: escalona
    assert exc_info.value.code == Intent.ESC_05_RECOGNITION_FAILURE.value


def test_destructive_action_requires_confirmation():
    fsm = CallFSM()
    fsm.route_intent(Intent.NET_04_REBOOT_CPE)
    state = fsm.request_action(Intent.NET_04_REBOOT_CPE)
    assert state == CallState.CONFIRMATION
    assert fsm.pending_confirmation is True

    # Sem confirmação, a ação não pode ir para EXECUTION diretamente.
    state = fsm.confirm_action(confirmed=True)
    assert state == CallState.EXECUTION


def test_declined_confirmation_does_not_execute():
    fsm = CallFSM()
    fsm.route_intent(Intent.FIN_03_TRUST_UNLOCK)
    fsm.request_action(Intent.FIN_03_TRUST_UNLOCK)
    state = fsm.confirm_action(confirmed=False)
    assert state == CallState.INTENT_ROUTING
    assert fsm.pending_confirmation is False


def test_non_destructive_action_skips_confirmation():
    fsm = CallFSM()
    fsm.route_intent(Intent.FIN_02_SECOND_COPY)
    state = fsm.request_action(Intent.FIN_02_SECOND_COPY)
    assert state == CallState.EXECUTION
    assert fsm.pending_confirmation is False


def test_stress_detection_escalates():
    fsm = CallFSM()
    with pytest.raises(EscalationRequired) as exc_info:
        fsm.report_stress_detected()
    assert exc_info.value.code == Intent.ESC_06_STRESS_DETECTED.value
