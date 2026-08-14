"""Estados, intents e regras invioláveis da FSM — spec §2.1, §2.2, §3.2, §7.1."""
from __future__ import annotations

from enum import StrEnum


class CallState(StrEnum):
    GREETING = "greeting"
    IDENTIFICATION = "identification"
    INTENT_ROUTING = "intent_routing"
    SLOT_COLLECTION = "slot_collection"
    CONFIRMATION = "confirmation"
    EXECUTION = "execution"
    CLOSING = "closing"
    ESCALATED = "escalated"


class Intent(StrEnum):
    # Tier 0 — Financeiro
    FIN_01_QUERY_INVOICES = "FIN-01"
    FIN_02_SECOND_COPY = "FIN-02"
    FIN_03_TRUST_UNLOCK = "FIN-03"
    FIN_04_PLAN_INFO = "FIN-04"
    FIN_05_CHANGE_DUE_DATE = "FIN-05"
    # Tier 0 — Conectividade
    NET_01_SESSION_DIAGNOSIS = "NET-01"
    NET_02_OPTICAL_DIAGNOSIS = "NET-02"
    NET_03_MASSIVE_CORRELATION = "NET-03"
    NET_04_REBOOT_CPE = "NET-04"
    NET_05_THROUGHPUT_TEST = "NET-05"
    NET_06_WIFI_DIAGNOSIS = "NET-06"
    # Tier 0 — Operacional
    OPS_01_SO_STATUS = "OPS-01"
    OPS_02_SO_SCHEDULE = "OPS-02"
    OPS_03_SO_CREATE = "OPS-03"
    OPS_04_MAINTENANCE_INFO = "OPS-04"
    OPS_05_PROTOCOL = "OPS-05"
    # Tier 1 — Escalonamento
    ESC_01_CANCELLATION = "ESC-01"
    ESC_02_COMPLAINT = "ESC-02"
    ESC_03_PHYSICAL_FAULT = "ESC-03"
    ESC_04_HUMAN_REQUEST = "ESC-04"
    ESC_05_RECOGNITION_FAILURE = "ESC-05"
    ESC_06_STRESS_DETECTED = "ESC-06"
    UNKNOWN = "UNKNOWN"


# Regra inviolável #1 (§7.1): sempre transborda, em qualquer estado.
ALWAYS_ESCALATE_INTENTS = frozenset({
    Intent.ESC_01_CANCELLATION,
    Intent.ESC_02_COMPLAINT,
    Intent.ESC_04_HUMAN_REQUEST,
})

# Regra inviolável #2 (§7.1): exige confirmação verbal explícita antes de executar.
DESTRUCTIVE_INTENTS = frozenset({
    Intent.NET_04_REBOOT_CPE,
    Intent.FIN_03_TRUST_UNLOCK,
    Intent.FIN_05_CHANGE_DUE_DATE,
    Intent.OPS_02_SO_SCHEDULE,
    Intent.OPS_03_SO_CREATE,
})

MAX_RECOGNITION_FAILURES_PER_SLOT = 2   # regra #3
MAX_TURNS_WITHOUT_PROGRESS = 3          # regra #4
MAX_SILENCE_MS_WITHOUT_FILLER = 1200    # regra #5
MAX_WORDS_PER_TURN_WITHOUT_PAUSE = 25   # regra #6
