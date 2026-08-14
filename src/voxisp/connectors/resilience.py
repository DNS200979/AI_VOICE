"""Resiliência para chamadas a APIs externas — spec §4.4.

"Regras de resiliência: timeout 2s por chamada externa · circuit breaker
(5 falhas → aberto por 30s) · cache Redis com TTL curto para dados de
sessão · degradação graciosa (se o ACS cair, ainda responde financeiro)."

Este módulo não depende de nenhum ERP específico — qualquer conector real
(Hubsoft, IXC, SGP, Voalle...) envolve suas chamadas HTTP com
`call_with_resilience` para herdar timeout, retry com backoff exponencial
e circuit breaker por padrão.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

DEFAULT_TIMEOUT_S = 2.0
DEFAULT_FAILURE_THRESHOLD = 5
DEFAULT_RECOVERY_S = 30.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_BACKOFF_BASE_S = 0.2


class CircuitOpenError(Exception):
    """Circuito aberto — a chamada falha rápido, sem tentar a rede.

    É o que permite a "degradação graciosa" do §4.4: se o ACS cair, o
    circuito dele abre e o orquestrador segue respondendo financeiro
    normalmente, sem ficar preso em timeout repetido.
    """


@dataclass
class CircuitBreaker:
    """Um circuito por conector/endpoint. Threshold e janela seguem o §4.4."""

    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD
    recovery_s: float = DEFAULT_RECOVERY_S
    _failure_count: int = field(default=0, init=False, repr=False)
    _opened_at: float | None = field(default=None, init=False, repr=False)

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        # Se a janela de recuperação expirou, permite tentar de novo (half-open).
        return time.monotonic() - self._opened_at < self.recovery_s

    def record_success(self) -> None:
        self._failure_count = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failure_count += 1
        if self._failure_count >= self.failure_threshold:
            self._opened_at = time.monotonic()


async def call_with_resilience[T](
    fn: Callable[[], Awaitable[T]],
    *,
    breaker: CircuitBreaker,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_base_s: float = DEFAULT_BACKOFF_BASE_S,
) -> T:
    """Executa `fn` com timeout duro, retry com backoff exponencial e
    circuit breaker. `fn` deve ser uma closure sem argumentos (ex.:
    `lambda: client.get(url)`)."""
    if breaker.is_open:
        raise CircuitOpenError("circuito aberto — chamada abortada sem ir à rede")

    attempt = 0
    while True:
        try:
            result = await asyncio.wait_for(fn(), timeout=timeout_s)
        except Exception:
            breaker.record_failure()
            if attempt >= max_retries:
                raise
            await asyncio.sleep(backoff_base_s * (2**attempt))
            attempt += 1
            continue
        else:
            breaker.record_success()
            return result
