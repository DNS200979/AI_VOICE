"""Allowlist de tools por estado da FSM — spec §4.3 e §3.2.

§4.3 (guardrails): "allowlist de tools por estado; validação de schema na
saída; bloqueio de qualquer conteúdo fora do domínio ISP".

§3.2: "A máquina de estados decide o que pode ser executado, em que ordem,
e o que exige confirmação. O LLM nunca chama uma tool destrutiva sem que a
FSM esteja no estado que a autoriza."

Tools só ficam disponíveis para o LLM no estado `EXECUTION` — nunca antes.
Isso não é arbitrário: pela FSM (`fsm/engine.py`), um intent destrutivo
(`DESTRUCTIVE_INTENTS`) só alcança `EXECUTION` depois de
`confirm_action(True)` — ou seja, depois da confirmação verbal explícita
exigida pela regra inviolável #2 (§7.1). Checar `state == EXECUTION` já
basta como guardrail: não existe caminho na FSM para uma tool destrutiva
ficar disponível sem confirmação.
"""
from __future__ import annotations

from voxisp.fsm.states import CallState, Intent

# Quais tools cada intent autoriza, uma vez em EXECUTION. Só cobre os
# intents com handler implementado nesta fase (spec §10) — os demais
# simplesmente não liberam nenhuma tool (allowlist vazia = LLM não vê
# nenhuma tool na request, não é uma checagem que pode falhar em runtime).
INTENT_TOOL_ALLOWLIST: dict[Intent, frozenset[str]] = {
    Intent.FIN_02_SECOND_COPY: frozenset({"get_invoices", "issue_second_copy"}),
    Intent.FIN_03_TRUST_UNLOCK: frozenset({"request_trust_unlock"}),
    Intent.NET_01_SESSION_DIAGNOSIS: frozenset({"get_connection_status", "get_cpe_diagnostics"}),
    Intent.NET_04_REBOOT_CPE: frozenset({"reboot_cpe"}),
    Intent.OPS_01_SO_STATUS: frozenset({"list_service_orders"}),
    # OPS-02 (agendar/reagendar/cancelar visita, spec §2.1) usa o método
    # dedicado `manage_visit` — não reaproveita mais `create_service_order`
    # (ver `turn_manager._handle_ops_02`/`_execute_visit_action`).
    Intent.OPS_02_SO_SCHEDULE: frozenset({"manage_visit"}),
    Intent.OPS_03_SO_CREATE: frozenset({"create_service_order"}),
}


def allowed_tools_for_state(state: CallState, intent: Intent | None) -> frozenset[str]:
    """Retorna o conjunto de nomes de tool que o LLM pode ver/chamar agora.

    Vazio em qualquer estado que não seja `EXECUTION`, ou se o intent não
    tiver tools mapeadas. O chamador (`ToolExecutor`) usa isso tanto para
    decidir quais tools declarar na request ao Claude (o modelo nunca vê as
    demais) quanto como segunda checagem antes de executar qualquer
    `tool_use` que o modelo devolva.
    """
    if intent is None or state != CallState.EXECUTION:
        return frozenset()
    return INTENT_TOOL_ALLOWLIST.get(intent, frozenset())
