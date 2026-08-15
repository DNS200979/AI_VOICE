"""Catálogo de tools do ISP Connector Hub expostas ao tool-calling do Claude.

Cada `ToolSpec.input_schema` só declara os campos que o MODELO preenche.
Dados que identificam o assinante (subscriber_id, cpe_serial) ou que
garantem idempotência (idempotency_key) NUNCA entram no schema — são
injetados pelo `ToolExecutor` a partir do contexto da chamada (§5:
idempotência; LGPD: nunca deixar o modelo escolher em qual assinante agir).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    connector_method: str  # nome do método correspondente em ISPConnector

    def to_claude_tool(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


_NO_ARGS_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}

TOOL_CATALOG: dict[str, ToolSpec] = {
    "get_invoices": ToolSpec(
        name="get_invoices",
        description=(
            "Consulta as faturas do assinante já identificado nesta ligação. "
            "Use status='open' para faturas em aberto."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["open", "paid", "all"],
                    "description": "Filtro de status da fatura",
                }
            },
            "required": ["status"],
            "additionalProperties": False,
        },
        connector_method="get_invoices",
    ),
    "issue_second_copy": ToolSpec(
        name="issue_second_copy",
        description=(
            "Emite a 2ª via (PIX copia-e-cola + linha digitável) de uma fatura "
            "específica, pelo ID retornado por get_invoices."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "ID da fatura retornado por get_invoices"}
            },
            "required": ["invoice_id"],
            "additionalProperties": False,
        },
        connector_method="issue_second_copy",
    ),
    "request_trust_unlock": ToolSpec(
        name="request_trust_unlock",
        description=(
            "Solicita o desbloqueio de confiança (acesso temporário) para o "
            "assinante desta ligação. Ação destrutiva — só disponível depois "
            "que o cliente confirmar verbalmente."
        ),
        input_schema=_NO_ARGS_SCHEMA,
        connector_method="request_trust_unlock",
    ),
    "get_connection_status": ToolSpec(
        name="get_connection_status",
        description=(
            "Consulta o status da sessão RADIUS/PPPoE do assinante: online, "
            "offline, motivo e horário da última desconexão."
        ),
        input_schema=_NO_ARGS_SCHEMA,
        connector_method="get_connection_status",
    ),
    "get_cpe_diagnostics": ToolSpec(
        name="get_cpe_diagnostics",
        description=(
            "Consulta o diagnóstico óptico/Wi-Fi do CPE (ONU) do assinante: "
            "status (online/LOS/dying gasp), potência óptica, canal Wi-Fi."
        ),
        input_schema=_NO_ARGS_SCHEMA,
        connector_method="get_cpe_diagnostics",
    ),
    "reboot_cpe": ToolSpec(
        name="reboot_cpe",
        description=(
            "Reinicia remotamente o roteador/CPE do assinante via TR-069. "
            "Ação destrutiva — só disponível depois que o cliente confirmar "
            "verbalmente."
        ),
        input_schema=_NO_ARGS_SCHEMA,
        connector_method="reboot_cpe",
    ),
    "list_service_orders": ToolSpec(
        name="list_service_orders",
        description="Lista as ordens de serviço (OS) do assinante desta ligação.",
        input_schema=_NO_ARGS_SCHEMA,
        connector_method="list_service_orders",
    ),
    "create_service_order": ToolSpec(
        name="create_service_order",
        description=(
            "Abre uma nova ordem de serviço para o assinante. Ação destrutiva "
            "— só disponível depois que o cliente confirmar verbalmente."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Categoria da OS, ex.: 'sem_sinal', 'wifi', 'lentidao'",
                },
                "summary": {"type": "string", "description": "Resumo do problema relatado pelo cliente"},
                "preferred_window": {
                    "type": "string",
                    "description": "Janela de horário preferida, se o cliente mencionou",
                },
            },
            "required": ["category", "summary"],
            "additionalProperties": False,
        },
        connector_method="create_service_order",
    ),
    "manage_visit": ToolSpec(
        name="manage_visit",
        description=(
            "Agenda, reagenda ou cancela a visita técnica (ordem de serviço) do "
            "assinante desta ligação. A AÇÃO (agendar/reagendar/cancelar) já foi "
            "decidida pela conversa antes desta chamada — não escolha a ação, só "
            "preencha os campos que ela precisa: window_start/window_end (data e "
            "hora em ISO 8601) quando for reagendamento; reason (motivo relatado "
            "pelo cliente, mínimo 10 caracteres) quando for cancelamento. Para "
            "agendamento simples, chame sem argumentos. Ação destrutiva — só "
            "disponível depois que o cliente confirmar verbalmente."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "window_start": {
                    "type": "string",
                    "description": "Data/hora de início da nova janela, ISO 8601 — só para reagendamento",
                },
                "window_end": {
                    "type": "string",
                    "description": "Data/hora de término da nova janela, ISO 8601 — só para reagendamento",
                },
                "reason": {
                    "type": "string",
                    "description": "Motivo do cancelamento relatado pelo cliente — só para cancelamento",
                },
            },
            "additionalProperties": False,
        },
        connector_method="manage_visit",
    ),
}
