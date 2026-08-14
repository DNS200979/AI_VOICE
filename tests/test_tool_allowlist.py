"""Testes do guardrail "allowlist de tools por estado" — spec §4.3/§3.2."""
from voxisp.fsm.states import CallState, Intent
from voxisp.orchestrator.tool_allowlist import allowed_tools_for_state


def test_execution_state_liberates_mapped_tools_for_net01():
    assert allowed_tools_for_state(CallState.EXECUTION, Intent.NET_01_SESSION_DIAGNOSIS) == {
        "get_connection_status",
        "get_cpe_diagnostics",
    }


def test_execution_state_liberates_mapped_tools_for_fin02():
    assert allowed_tools_for_state(CallState.EXECUTION, Intent.FIN_02_SECOND_COPY) == {
        "get_invoices",
        "issue_second_copy",
    }


def test_destructive_tool_only_available_in_execution_never_confirmation():
    """A tool destrutiva de FIN-03 só aparece em EXECUTION — e a FSM só
    alcança EXECUTION para um intent destrutivo depois de confirm_action(True)
    (fsm/engine.py). Em CONFIRMATION (aguardando a resposta do cliente), a
    allowlist tem que estar vazia."""
    assert allowed_tools_for_state(CallState.CONFIRMATION, Intent.FIN_03_TRUST_UNLOCK) == frozenset()
    assert allowed_tools_for_state(CallState.EXECUTION, Intent.FIN_03_TRUST_UNLOCK) == {"request_trust_unlock"}


def test_no_tools_before_execution():
    for state in (CallState.GREETING, CallState.IDENTIFICATION, CallState.INTENT_ROUTING, CallState.SLOT_COLLECTION):
        assert allowed_tools_for_state(state, Intent.NET_01_SESSION_DIAGNOSIS) == frozenset()


def test_no_tools_without_intent():
    assert allowed_tools_for_state(CallState.EXECUTION, None) == frozenset()


def test_no_tools_for_unmapped_intent():
    # ESC-04 nunca deveria ter tools (é sempre transbordo) — nem está no
    # catálogo de allowlist.
    assert allowed_tools_for_state(CallState.EXECUTION, Intent.ESC_04_HUMAN_REQUEST) == frozenset()


def test_no_tools_after_escalation_or_closing():
    for state in (CallState.ESCALATED, CallState.CLOSING):
        assert allowed_tools_for_state(state, Intent.NET_01_SESSION_DIAGNOSIS) == frozenset()
