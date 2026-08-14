"""Cliente LLM do orquestrador — spec §4.3.

Papéis do LLM (nunca dirige a conversa livremente, ver §3.2):
1. Classificador de intenção -> um dos ~25 intents de `voxisp.fsm.states.Intent`
2. Extrator de entidades -> CPF, protocolo, endereço, data
3. Redator do turno -> frase de resposta a partir de um slot já resolvido pela FSM

Timeout duro de 1,5s (spec §4.3): estourou, usa frase de espera
pré-gravada enquanto a chamada continua em segundo plano.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Protocol

from voxisp.fsm.states import Intent

LLM_TIMEOUT_S = 1.5
HOLD_MESSAGE = "Só um instante, estou consultando..."


@dataclass
class IntentClassification:
    intent: Intent
    entities: dict[str, str] = field(default_factory=dict)
    confidence: float = 0.0


class LLMClient(Protocol):
    async def classify_intent(self, utterance: str, context: dict) -> IntentClassification: ...

    async def draft_turn(self, template: str, slots: dict) -> str: ...


class StubLLMClient:
    """Classificador determinístico por palavra-chave — sem chamada de rede.

    Não substitui um LLM real (Claude Haiku / GPT-4o-mini / Gemini Flash,
    ver spec §4.3) em produção, mas permite testar a FSM e o Connector Hub
    ponta a ponta sem custo/latência de API e sem credenciais.
    """

    _KEYWORDS: ClassVar[dict[Intent, tuple[str, ...]]] = {
        Intent.ESC_04_HUMAN_REQUEST: ("atendente", "pessoa", "humano"),
        Intent.ESC_01_CANCELLATION: ("cancelar", "cancelamento"),
        Intent.ESC_02_COMPLAINT: ("anatel", "procon", "reclamação"),
        Intent.FIN_02_SECOND_COPY: ("boleto", "2 via", "segunda via", "fatura"),
        Intent.FIN_03_TRUST_UNLOCK: ("desbloqueio", "confiança", "desbloquear"),
        Intent.NET_04_REBOOT_CPE: ("reiniciar", "reboot", "resetar roteador"),
        Intent.NET_01_SESSION_DIAGNOSIS: (
            "sem internet", "caiu", "sem sinal", "sem acesso", "internet parou", "parou de funcionar",
        ),
        Intent.OPS_01_SO_STATUS: ("status da ordem", "status da os", "técnico"),
    }

    async def classify_intent(self, utterance: str, context: dict) -> IntentClassification:
        lowered = utterance.lower()
        for intent, keywords in self._KEYWORDS.items():
            if any(kw in lowered for kw in keywords):
                return IntentClassification(intent=intent, confidence=0.9)
        return IntentClassification(intent=Intent.UNKNOWN, confidence=0.0)

    async def draft_turn(self, template: str, slots: dict) -> str:
        # Nunca gera número por conta própria (spec §12, risco de alucinação
        # financeira) — apenas preenche o template travado com o slot da FSM.
        return template.format(**slots)
