"""Logging estruturado — base para o trace por turno citado na spec §3
(Langfuse/OTel) e para o dashboard operacional do §9 (Grafana).

Nesta v1, apenas configura `structlog` com saída JSON e mascaramento
básico de CPF (LGPD §6.3 — "não persistir CPF em claro nos logs").
"""
from __future__ import annotations

import logging
import re

import structlog

from voxisp.config import settings

_CPF_PATTERN = re.compile(r"\b(\d{3})\.?(\d{3})\.?(\d{3})-?(\d{2})\b")


def _mask_cpf(_logger, _method_name, event_dict):
    for key, value in list(event_dict.items()):
        if isinstance(value, str):
            event_dict[key] = _CPF_PATTERN.sub(r"\1.***.**\4", value)
    return event_dict


def configure_logging() -> None:
    logging.basicConfig(level=settings.log_level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _mask_cpf,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(settings.log_level)),
    )


def get_logger(name: str) -> structlog.BoundLogger:
    return structlog.get_logger(name)
