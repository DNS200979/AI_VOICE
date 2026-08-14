"""Motor da FSM — spec §3.2.

O LLM nunca dirige a conversa livremente: ele classifica intenção, extrai
entidades e redige o turno. É a `CallFSM` que decide o que pode ser
executado, em que ordem, e o que exige confirmação. Nenhuma tool
destrutiva é chamada fora do estado que a autoriza.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from voxisp.fsm.states import (
    ALWAYS_ESCALATE_INTENTS,
    DESTRUCTIVE_INTENTS,
    MAX_RECOGNITION_FAILURES_PER_SLOT,
    MAX_TURNS_WITHOUT_PROGRESS,
    CallState,
    Intent,
)


class EscalationRequired(Exception):
    """Levantada pela FSM quando uma regra inviolável dispara o transbordo."""

    def __init__(self, reason: str, code: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


@dataclass
class CallFSM:
    """Estado de uma chamada em andamento. Uma instância por ligação."""

    state: CallState = CallState.GREETING
    subscriber_id: str | None = None
    current_intent: Intent | None = None
    pending_confirmation: bool = False
    escalation_reason: str | None = None

    _recognition_failures: dict[str, int] = field(default_factory=dict)
    _turns_without_progress: int = 0
    _last_state_snapshot: CallState | None = None

    # -- Regra #1: DTMF 0 / "atendente" / "pessoa" / "humano" ------------
    def request_human(self, reason: str = "solicitação explícita do cliente") -> None:
        self._escalate(reason, Intent.ESC_04_HUMAN_REQUEST.value)

    # -- Roteamento de intenção -------------------------------------------
    def route_intent(self, intent: Intent) -> None:
        if intent in ALWAYS_ESCALATE_INTENTS:
            self._escalate(f"intenção {intent.value} exige atendimento humano", intent.value)
            return
        self.current_intent = intent
        self._advance(CallState.SLOT_COLLECTION)

    # -- Regra #3: falha de reconhecimento no slot ------------------------
    def record_recognition_failure(self, slot_name: str) -> None:
        count = self._recognition_failures.get(slot_name, 0) + 1
        self._recognition_failures[slot_name] = count
        if count >= MAX_RECOGNITION_FAILURES_PER_SLOT:
            self._escalate(
                f"{count} falhas de reconhecimento consecutivas no slot '{slot_name}'",
                Intent.ESC_05_RECOGNITION_FAILURE.value,
            )

    def reset_recognition_failures(self, slot_name: str) -> None:
        self._recognition_failures.pop(slot_name, None)

    # -- Regra #6: detecção de estresse/raiva (delegado ao pipeline de áudio) --
    def report_stress_detected(self) -> None:
        self._escalate("estresse/raiva detectado na prosódia", Intent.ESC_06_STRESS_DETECTED.value)

    # -- Regra #2: ação destrutiva exige confirmação verbal explícita ----
    def request_action(self, intent: Intent) -> CallState:
        """Chamado quando todos os slots foram coletados e a FSM decide
        se a ação pode ser executada direto ou precisa de confirmação."""
        if intent in DESTRUCTIVE_INTENTS and not self.pending_confirmation:
            self.pending_confirmation = True
            self._advance(CallState.CONFIRMATION)
            return self.state
        self._advance(CallState.EXECUTION)
        return self.state

    def confirm_action(self, confirmed: bool) -> CallState:
        if self.state != CallState.CONFIRMATION:
            raise RuntimeError("confirm_action chamado fora do estado CONFIRMATION")
        if not confirmed:
            # Cliente recusou a ação — volta para roteamento de intenção, não executa.
            self.pending_confirmation = False
            self._advance(CallState.INTENT_ROUTING)
            return self.state
        self.pending_confirmation = False
        self._advance(CallState.EXECUTION)
        return self.state

    def complete_execution(self) -> None:
        self._advance(CallState.CLOSING)

    # -- Regra #4: progresso de estado ------------------------------------
    def _advance(self, new_state: CallState) -> None:
        if new_state == self.state:
            self._turns_without_progress += 1
            if self._turns_without_progress > MAX_TURNS_WITHOUT_PROGRESS:
                self._escalate(
                    f"{self._turns_without_progress} turnos sem progresso de estado",
                    "ESC-STALL",
                )
                return
        else:
            self._turns_without_progress = 0
        self.state = new_state

    def _escalate(self, reason: str, code: str) -> None:
        self.escalation_reason = f"[{code}] {reason}"
        self.state = CallState.ESCALATED
        raise EscalationRequired(reason, code)
