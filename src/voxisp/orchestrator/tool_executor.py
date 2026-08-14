"""Loop real de tool-calling do Claude, restrito ao allowlist da FSM — spec §3.2/§4.3.

Diferente do `ClaudeLLMClient.classify_intent` (que só classifica intenção
com saída estruturada, sem tools), aqui o modelo decide QUAIS ferramentas
chamar e QUANDO parar — mas só dentro do que
`tool_allowlist.allowed_tools_for_state` autoriza para o estado atual da
FSM. É a implementação literal do §3.2: "O LLM nunca chama uma tool
destrutiva sem que a FSM esteja no estado que a autoriza."

Duas camadas de defesa contra tool fora do allowlist:
1. Só declaramos ao Claude as tools permitidas (`tools=[...]` na request) —
   o modelo fisicamente não vê as demais.
2. Mesmo assim, se um `tool_use.name` chegar fora do allowlist (bug de SDK,
   drift de versão etc.), o executor recusa com um `tool_result` de erro e
   NUNCA chama o conector.

Importante: o texto final que o modelo escreve (`ToolExecutionResult.final_text`)
não é necessariamente o que vai para o cliente. Para fluxos com valor
financeiro, o chamador (`CallOrchestrator`) usa os `diagnostics`
retornados aqui — não o texto livre — para montar a resposta via template
travado, preservando a regra do §12 (LLM nunca gera número por conta
própria).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from voxisp.connectors.base import ISPConnector
from voxisp.connectors.models import SODraft, Subscriber
from voxisp.fsm.states import CallState, Intent
from voxisp.orchestrator.tool_allowlist import allowed_tools_for_state
from voxisp.orchestrator.tools import TOOL_CATALOG, ToolSpec

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic

LLM_TOOL_TIMEOUT_S = 1.5
MAX_TOOL_LOOP_TURNS = 4


class ToolAllowlistViolation(Exception):
    """Nenhuma tool está autorizada para o intent/estado atual — o loop
    nem chega a consultar o Claude."""


@dataclass
class ToolExecutionResult:
    tool_calls: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    final_text: str = ""


class ToolExecutor:
    """Executa o loop de tool-calling contra o Claude, restrito ao
    allowlist do estado atual da FSM (`tool_allowlist.py`)."""

    def __init__(
        self,
        client: AsyncAnthropic,
        connector: ISPConnector,
        model: str = "claude-haiku-4-5",
        timeout_s: float = LLM_TOOL_TIMEOUT_S,
        max_turns: int = MAX_TOOL_LOOP_TURNS,
    ) -> None:
        self._client = client
        self._connector = connector
        self._model = model
        self._timeout_s = timeout_s
        self._max_turns = max_turns

    async def run(
        self,
        *,
        subscriber: Subscriber,
        intent: Intent,
        state: CallState,
        instructions: str,
    ) -> ToolExecutionResult:
        allowed = allowed_tools_for_state(state, intent)
        if not allowed:
            raise ToolAllowlistViolation(
                f"nenhuma tool permitida para intent={intent!r} no estado {state!r}"
            )

        tool_defs = [spec.to_claude_tool() for name, spec in TOOL_CATALOG.items() if name in allowed]
        messages: list[dict] = [{"role": "user", "content": instructions}]
        result = ToolExecutionResult()

        for _ in range(self._max_turns):
            response = await self._client.with_options(timeout=self._timeout_s).messages.create(
                model=self._model,
                max_tokens=1024,
                tools=tool_defs,
                messages=messages,
            )
            messages.append({"role": "assistant", "content": response.content})

            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            if not tool_use_blocks:
                result.final_text = next((b.text for b in response.content if b.type == "text"), "")
                return result

            tool_results = []
            for block in tool_use_blocks:
                if block.name not in allowed:
                    # Defesa em profundidade: não deveria disparar, já que só
                    # declaramos tools permitidas acima — mas se disparar,
                    # nunca executa o conector.
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": "tool não permitida no estado atual da chamada",
                            "is_error": True,
                        }
                    )
                    continue

                spec = TOOL_CATALOG[block.name]
                try:
                    output = await self._execute(spec, subscriber, block.input)
                except Exception as exc:  # noqa: BLE001 - erro de conector vira tool_result de erro
                    tool_results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": str(exc), "is_error": True}
                    )
                    continue

                result.tool_calls.append(block.name)
                result.diagnostics[block.name] = output
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(output, default=str)}
                )
            messages.append({"role": "user", "content": tool_results})

        raise TimeoutError(f"loop de tool-calling excedeu {self._max_turns} turnos sem concluir")

    async def _execute(self, spec: ToolSpec, subscriber: Subscriber, model_input: dict) -> dict:
        """Injeta dados de contexto (subscriber_id, cpe_serial,
        idempotency_key) que o modelo NUNCA controla — só os campos
        declarados no `input_schema` da tool vêm de `model_input`."""
        connector = self._connector
        name = spec.connector_method

        if name == "get_invoices":
            invoices = await connector.get_invoices(subscriber.id, status=model_input.get("status", "open"))
            return {"invoices": [i.model_dump(mode="json") for i in invoices]}
        if name == "issue_second_copy":
            payload = await connector.issue_second_copy(model_input["invoice_id"])
            return payload.model_dump(mode="json")
        if name == "request_trust_unlock":
            trust = await connector.request_trust_unlock(subscriber.id)
            return trust.model_dump(mode="json")
        if name == "get_connection_status":
            status = await connector.get_connection_status(subscriber.id)
            return status.model_dump(mode="json")
        if name == "get_cpe_diagnostics":
            if not subscriber.cpe_serial:
                return {"error": "assinante sem CPE cadastrado"}
            diag = await connector.get_cpe_diagnostics(subscriber.cpe_serial)
            return diag.model_dump(mode="json")
        if name == "reboot_cpe":
            if not subscriber.cpe_serial:
                return {"error": "assinante sem CPE cadastrado"}
            idem_key = f"reboot:{subscriber.id}:{uuid.uuid4().hex[:8]}"
            action = await connector.reboot_cpe(subscriber.cpe_serial, idempotency_key=idem_key)
            return action.model_dump(mode="json")
        if name == "list_service_orders":
            orders = await connector.list_service_orders(subscriber.id)
            return {"service_orders": [o.model_dump(mode="json") for o in orders]}
        if name == "create_service_order":
            draft = SODraft(
                subscriber_id=subscriber.id,
                category=model_input["category"],
                summary=model_input["summary"],
                preferred_window=model_input.get("preferred_window"),
            )
            service_order = await connector.create_service_order(draft)
            return service_order.model_dump(mode="json")

        raise ValueError(f"tool '{spec.name}' sem dispatcher implementado em ToolExecutor._execute")


def get_tool_executor(provider: str, settings: Any, connector: ISPConnector) -> ToolExecutor | None:
    """Fábrica no mesmo estilo de `get_connector`/`get_llm_client`. Retorna
    `None` para `provider != 'anthropic'` — tool-calling real é opt-in."""
    if provider != "anthropic":
        return None
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.llm_api_key or None)
    return ToolExecutor(client=client, connector=connector, model=settings.llm_model)
