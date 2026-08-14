"""Orquestrador de chamada — junta FSM + LLM + ISP Connector Hub.

Escopo desta v1: Fase 1 do roadmap (spec §10) — identificação, FIN-01→03,
NET-01→03 (com o fluxo de exemplo do §7.2), protocolo (OPS-05) e
transbordo contextualizado (§7.3). Os demais intents do catálogo entram
nas fases seguintes, seguindo o mesmo padrão de handler.

Este objeto é o ponto de integração com o voice runtime real (LiveKit
Agents / Pipecat, spec §3): ele recebe texto já transcrito pelo ASR e
devolve texto para o TTS, sem saber nada de áudio.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import ClassVar

from voxisp.connectors.base import ISPConnector
from voxisp.connectors.models import ConnectionStatus, Subscriber
from voxisp.fsm.engine import CallFSM, EscalationRequired
from voxisp.fsm.states import CallState, Intent
from voxisp.massive_detection import check_massive_incident
from voxisp.orchestrator.llm_client import LLMClient


def _digits_only(text: str) -> str:
    return re.sub(r"\D", "", text)


def _validate_cpf(cpf: str) -> bool:
    """Validação de DV do CPF (spec §4.1 — "validação de CPF por dígito verificador")."""
    cpf = _digits_only(cpf)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for i in (9, 10):
        total = sum(int(cpf[num]) * ((i + 1) - num) for num in range(i))
        digit = (total * 10 % 11) % 10
        if digit != int(cpf[i]):
            return False
    return True


@dataclass
class TurnLogEntry:
    role: str  # "customer" | "assistant"
    text: str


@dataclass
class EscalationPayload:
    """Payload do transbordo — spec §7.3. Isso é o que aparece na tela do
    atendente humano antes de ele atender."""

    subscriber: Subscriber | None
    transcript_summary: list[str]
    diagnostics_executed: dict[str, object]
    intent: Intent | None
    reason: str
    protocol_number: str | None


@dataclass
class TurnResult:
    text: str
    escalate: bool = False
    escalation: EscalationPayload | None = None
    protocol_number: str | None = None


@dataclass
class CallOrchestrator:
    connector: ISPConnector
    llm: LLMClient
    fsm: CallFSM = field(default_factory=CallFSM)
    subscriber: Subscriber | None = None
    _transcript: list[TurnLogEntry] = field(default_factory=list)
    _diagnostics: dict[str, object] = field(default_factory=dict)
    _protocol_number: str | None = None

    async def greet(self) -> TurnResult:
        text = (
            "Provedor, assistente virtual. Sua ligação é gravada. "
            "Me diga seu CPF, por favor."
        )
        self._log("assistant", text)
        self.fsm.state = CallState.IDENTIFICATION
        return TurnResult(text=text)

    async def identify(self, spoken_cpf: str) -> TurnResult:
        self._log("customer", spoken_cpf)
        if not _validate_cpf(spoken_cpf):
            self.fsm.record_recognition_failure("cpf")
            return TurnResult(text="Não consegui validar esse CPF. Pode repetir, por favor?")

        subscriber = await self.connector.find_subscriber(cpf=_digits_only(spoken_cpf))
        if subscriber is None:
            return await self._escalate("CPF não localizado na base", Intent.UNKNOWN)

        self.subscriber = subscriber
        self.fsm.subscriber_id = subscriber.id
        self.fsm.reset_recognition_failures("cpf")
        self.fsm.state = CallState.INTENT_ROUTING
        text = f"Obrigada, {subscriber.name.split()[0]}. Como posso ajudar?"
        self._log("assistant", text)
        return TurnResult(text=text)

    async def handle_utterance(self, utterance: str) -> TurnResult:
        self._log("customer", utterance)
        # Regra inviolável #1 — checada antes de qualquer classificação de intenção.
        if any(kw in utterance.lower() for kw in ("atendente", "pessoa", "humano")):
            return await self._escalate("solicitação explícita de atendente", Intent.ESC_04_HUMAN_REQUEST)

        classification = await self.llm.classify_intent(utterance, context={"subscriber": self.subscriber})
        try:
            self.fsm.route_intent(classification.intent)
        except EscalationRequired as exc:
            return await self._escalate(exc.reason, classification.intent)

        handler = self._HANDLERS.get(classification.intent)
        if handler is None:
            return await self._escalate(
                f"intent '{classification.intent}' sem handler implementado nesta fase", classification.intent
            )
        return await handler(self)

    # -- Handlers por intent (Fase 1 do roadmap, spec §10) -----------------

    async def _handle_fin_02(self) -> TurnResult:
        assert self.subscriber is not None
        invoices = await self.connector.get_invoices(self.subscriber.id, status="open")
        self._diagnostics["FIN-02.get_invoices"] = [i.model_dump(mode="json") for i in invoices]
        if not invoices:
            text = "Não encontrei faturas em aberto no seu cadastro. Posso ajudar em algo mais?"
            self._log("assistant", text)
            return TurnResult(text=text)

        payload = await self.connector.issue_second_copy(invoices[0].id)
        self._diagnostics["FIN-02.issue_second_copy"] = payload.model_dump(mode="json")
        text = (
            f"Vou te enviar por WhatsApp o PIX copia-e-cola e a linha digitável da fatura "
            f"de R$ {payload.amount_cents / 100:.2f}, vencimento {payload.due_date.strftime('%d/%m')}."
        )
        self._log("assistant", text)
        return TurnResult(text=text)

    async def _handle_fin_03(self) -> TurnResult:
        assert self.subscriber is not None
        result = await self.connector.request_trust_unlock(self.subscriber.id)
        self._diagnostics["FIN-03.request_trust_unlock"] = result.model_dump(mode="json")
        if not result.eligible:
            text = f"Não consigo liberar o desbloqueio agora: {result.reason}."
        else:
            text = "Vou liberar seu acesso por 48 horas. Confirma o desbloqueio de confiança?"
            self.fsm.request_action(Intent.FIN_03_TRUST_UNLOCK)
        self._log("assistant", text)
        return TurnResult(text=text)

    async def _handle_net_01(self) -> TurnResult:
        """Fluxo "estou sem internet" completo, ver exemplo em spec §7.2."""
        assert self.subscriber is not None
        conn_status: ConnectionStatus = await self.connector.get_connection_status(self.subscriber.id)
        self._diagnostics["NET-01.get_connection_status"] = conn_status.model_dump(mode="json")

        if self.subscriber.cpe_serial:
            diag = await self.connector.get_cpe_diagnostics(self.subscriber.cpe_serial)
            self._diagnostics["NET-02.get_cpe_diagnostics"] = diag.model_dump(mode="json")

        massive = await check_massive_incident(self.connector, self.subscriber)
        if massive.is_massive and massive.incident:
            self._diagnostics["NET-03.massive_check"] = {
                "affected_count": massive.incident.affected_count,
                "eta": massive.eta_message,
            }
            protocol = await self.connector.create_protocol(
                self.subscriber.id, "Incidente massivo NET-03 — cliente informado, sem OS individual"
            )
            self._protocol_number = protocol.protocol_number
            text = (
                f"Identifiquei uma interrupção que afeta sua região. Nossa equipe já está no local, "
                f"com previsão de normalização até as {massive.eta_message}. "
                f"Seu protocolo é {protocol.protocol_number}."
            )
            self.fsm.complete_execution()
            self._log("assistant", text)
            return TurnResult(text=text, protocol_number=protocol.protocol_number)

        # Caso individual — sem massivo, segue diagnóstico ponto a ponto.
        protocol = await self.connector.create_protocol(self.subscriber.id, "NET-01 diagnóstico individual")
        self._protocol_number = protocol.protocol_number
        if conn_status.session_state.value == "offline":
            text = (
                f"Sua conexão está offline desde {conn_status.last_logoff:%H:%M}, motivo: "
                f"{conn_status.disconnect_reason}. Vou abrir uma ordem de serviço técnica. "
                f"Seu protocolo é {protocol.protocol_number}."
            )
        else:
            text = (
                f"Sua sessão está ativa — o problema pode ser no Wi-Fi ou no roteador. "
                f"Posso reiniciar seu equipamento remotamente, pode ser? Protocolo {protocol.protocol_number}."
            )
        self.fsm.complete_execution()
        self._log("assistant", text)
        return TurnResult(text=text, protocol_number=protocol.protocol_number)

    async def _handle_ops_01(self) -> TurnResult:
        assert self.subscriber is not None
        orders = await self.connector.list_service_orders(self.subscriber.id)
        self._diagnostics["OPS-01.list_service_orders"] = [o.model_dump(mode="json") for o in orders]
        if not orders:
            text = "Não encontrei ordens de serviço em aberto no seu cadastro."
        else:
            so = orders[-1]
            text = f"Sua ordem de serviço está com status '{so.status.value}'."
        self._log("assistant", text)
        return TurnResult(text=text)

    _HANDLERS: ClassVar[dict] = {
        Intent.FIN_02_SECOND_COPY: _handle_fin_02,
        Intent.FIN_03_TRUST_UNLOCK: _handle_fin_03,
        Intent.NET_01_SESSION_DIAGNOSIS: _handle_net_01,
        Intent.OPS_01_SO_STATUS: _handle_ops_01,
    }

    # -- Transbordo (spec §7.3) ---------------------------------------------

    async def _escalate(self, reason: str, intent: Intent) -> TurnResult:
        protocol_number = self._protocol_number
        if protocol_number is None and self.subscriber is not None:
            protocol = await self.connector.create_protocol(self.subscriber.id, f"Transbordo: {reason}")
            protocol_number = protocol.protocol_number

        payload = EscalationPayload(
            subscriber=self.subscriber,
            transcript_summary=[f"{e.role}: {e.text}" for e in self._transcript],
            diagnostics_executed=dict(self._diagnostics),
            intent=intent,
            reason=reason,
            protocol_number=protocol_number,
        )
        text = f"Vou te transferir para um atendente humano. Protocolo {protocol_number}."
        self._log("assistant", text)
        return TurnResult(text=text, escalate=True, escalation=payload, protocol_number=protocol_number)

    def _log(self, role: str, text: str) -> None:
        self._transcript.append(TurnLogEntry(role=role, text=text))
