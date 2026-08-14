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
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

from pydantic import BaseModel, Field

from voxisp.fsm.states import Intent

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic

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


# ---------------------------------------------------------------------------
# Implementação real — Claude (spec §4.3)
# ---------------------------------------------------------------------------

_INTENT_CATALOG_PT: dict[Intent, str] = {
    Intent.FIN_01_QUERY_INVOICES: "consulta de faturas em aberto, valor e vencimento",
    Intent.FIN_02_SECOND_COPY: "pedido de 2ª via de boleto/fatura (PIX ou linha digitável)",
    Intent.FIN_03_TRUST_UNLOCK: "desbloqueio de confiança / promessa de pagamento",
    Intent.FIN_04_PLAN_INFO: "consulta de plano contratado, data de adesão, fidelidade",
    Intent.FIN_05_CHANGE_DUE_DATE: "alteração da data de vencimento da fatura",
    Intent.NET_01_SESSION_DIAGNOSIS: 'cliente relata "sem internet" / "caiu" / "sem sinal"',
    Intent.NET_02_OPTICAL_DIAGNOSIS: "diagnóstico óptico da ONU (luz vermelha, sinal)",
    Intent.NET_03_MASSIVE_CORRELATION: "pergunta sobre interrupção/incidente na região",
    Intent.NET_04_REBOOT_CPE: "pedido para reiniciar o roteador/CPE remotamente",
    Intent.NET_05_THROUGHPUT_TEST: "teste ou consulta de velocidade/throughput",
    Intent.NET_06_WIFI_DIAGNOSIS: "problema de Wi-Fi (alcance, canal, muitos dispositivos)",
    Intent.OPS_01_SO_STATUS: "status de ordem de serviço (OS) já aberta",
    Intent.OPS_02_SO_SCHEDULE: "agendar, reagendar ou cancelar visita técnica",
    Intent.OPS_03_SO_CREATE: "abertura de uma nova ordem de serviço",
    Intent.OPS_04_MAINTENANCE_INFO: "pergunta sobre manutenção programada na região",
    Intent.OPS_05_PROTOCOL: "pedido de emissão ou consulta de número de protocolo",
    Intent.ESC_01_CANCELLATION: "pedido de cancelamento do contrato/serviço",
    Intent.ESC_02_COMPLAINT: "reclamação formal ou menção a Anatel/Procon",
    Intent.ESC_03_PHYSICAL_FAULT: "falha física já confirmada (ex.: fibra rompida no drop)",
    Intent.ESC_04_HUMAN_REQUEST: "pedido explícito para falar com atendente humano",
    Intent.UNKNOWN: "nenhuma das intenções acima — fora do domínio ISP ou ambíguo",
}


def _build_system_prompt() -> str:
    catalog_lines = "\n".join(f"- {intent.value}: {desc}" for intent, desc in _INTENT_CATALOG_PT.items())
    return (
        "Você é o classificador de intenção do VOX-ISP, um atendente virtual de voz "
        "para provedores de internet brasileiros. Sua única tarefa é classificar a "
        "fala do cliente em UM dos códigos de intenção abaixo e extrair entidades "
        "relevantes (CPF, protocolo, endereço, data). Você NUNCA redige a resposta "
        "ao cliente nem gera valores financeiros — isso é feito por outro "
        "componente a partir de dados reais do ERP.\n\n"
        "Catálogo de intenções:\n"
        f"{catalog_lines}\n\n"
        "Regras:\n"
        "- Use exatamente um dos códigos acima em `intent`. Nunca invente um código novo.\n"
        "- Não use ESC-05 nem ESC-06 — são gerados internamente pelo sistema "
        "(falha de reconhecimento e detecção de estresse), nunca a partir do texto.\n"
        "- Se a fala não se encaixar claramente em nenhuma intenção, responda UNKNOWN.\n"
        "- `confidence` é sua confiança de 0.0 a 1.0 na classificação escolhida."
    )


class IntentClassificationSchema(BaseModel):
    """Saída estruturada do classificador — spec §4.3 (guardrails: "validação
    de schema na saída"). Usar `output_config.format`/`messages.parse` garante
    que o modelo nunca devolva um intent fora do catálogo."""

    intent: Intent
    entities: dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)


class ClaudeLLMClient:
    """Classificador de intenção real via Claude — spec §4.3.

    Modelo padrão: Haiku 4.5 — a spec pede explicitamente "modelo rápido para
    classificação/redação" dado o orçamento de latência do turno (§5.1: ~180ms
    para classificação+tool call) e o timeout duro de 1,5s. Trocável via
    `LLM_MODEL` no `.env` para Sonnet/Opus em casos ambíguos (spec: "modelo
    maior só para o classificador em casos ambíguos").

    `draft_turn` NUNCA chama o LLM: a spec exige template travado para
    qualquer valor financeiro/técnico (§12, risco de alucinação) — o mesmo
    comportamento determinístico do `StubLLMClient`.
    """

    def __init__(
        self,
        client: AsyncAnthropic,
        model: str = "claude-haiku-4-5",
        timeout_s: float = LLM_TIMEOUT_S,
    ) -> None:
        self._client = client
        self._model = model
        self._timeout_s = timeout_s
        # O catálogo é estático entre chamadas — cache_control permite reuso
        # de prompt cache quando o modelo suportar (Haiku 4.5 exige prefixo
        # mínimo de 4096 tokens para cachear; sem efeito abaixo disso, sem
        # custo extra). Ver shared/prompt-caching.md.
        self._system = [
            {"type": "text", "text": _build_system_prompt(), "cache_control": {"type": "ephemeral"}}
        ]

    async def classify_intent(self, utterance: str, context: dict) -> IntentClassification:
        try:
            response = await self._client.with_options(timeout=self._timeout_s).messages.parse(
                model=self._model,
                max_tokens=256,
                system=self._system,
                messages=[{"role": "user", "content": utterance}],
                output_format=IntentClassificationSchema,
            )
        except Exception:  # noqa: BLE001 - proposital: timeout/erro de API/validação de
            # schema degradam para UNKNOWN em vez de propagar (spec §4.3). O
            # orquestrador trata UNKNOWN como transbordo — nunca inventa uma
            # classificação nem derruba a chamada por falha do LLM.
            return IntentClassification(intent=Intent.UNKNOWN, confidence=0.0)

        parsed = response.parsed_output
        return IntentClassification(intent=parsed.intent, entities=parsed.entities, confidence=parsed.confidence)

    async def draft_turn(self, template: str, slots: dict) -> str:
        return template.format(**slots)


def get_llm_client(name: str = "stub", settings: Any = None) -> LLMClient:
    """Fábrica de clientes LLM — mesmo padrão de `voxisp.connectors.get_connector`."""
    if name == "stub":
        return StubLLMClient()
    if name == "anthropic":
        import anthropic

        if settings is None:
            from voxisp.config import settings as default_settings

            settings = default_settings
        client = anthropic.AsyncAnthropic(api_key=settings.llm_api_key or None)
        return ClaudeLLMClient(client=client, model=settings.llm_model)
    raise ValueError(f"LLM_PROVIDER '{name}' desconhecido. Disponíveis: stub, anthropic.")
