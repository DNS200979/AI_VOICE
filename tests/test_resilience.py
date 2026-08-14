"""Testes da camada de resiliência — spec §4.4 (timeout, retry, circuit breaker)."""
import asyncio

import pytest

from voxisp.connectors.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    call_with_resilience,
)


async def test_successful_call_keeps_circuit_closed():
    breaker = CircuitBreaker()

    async def ok():
        return "resultado"

    result = await call_with_resilience(ok, breaker=breaker)
    assert result == "resultado"
    assert breaker.is_open is False


async def test_circuit_opens_after_failure_threshold():
    breaker = CircuitBreaker(failure_threshold=3, recovery_s=60)

    async def always_fails():
        raise RuntimeError("falha simulada")

    # 3 falhas (sem retry, para isolar o comportamento do breaker) -> abre.
    for _ in range(3):
        with pytest.raises(RuntimeError):
            await call_with_resilience(always_fails, breaker=breaker, max_retries=0)

    assert breaker.is_open is True

    # Com o circuito aberto, nem tenta chamar `fn` de novo.
    with pytest.raises(CircuitOpenError):
        await call_with_resilience(always_fails, breaker=breaker, max_retries=0)


async def test_circuit_half_opens_after_recovery_window():
    breaker = CircuitBreaker(failure_threshold=1, recovery_s=0.05)

    async def always_fails():
        raise RuntimeError("falha simulada")

    with pytest.raises(RuntimeError):
        await call_with_resilience(always_fails, breaker=breaker, max_retries=0)
    assert breaker.is_open is True

    await asyncio.sleep(0.06)
    assert breaker.is_open is False  # janela expirou — permite tentar de novo


async def test_retry_recovers_from_transient_failure():
    breaker = CircuitBreaker()
    attempts = {"count": 0}

    async def flaky():
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise RuntimeError("transitório")
        return "ok na segunda tentativa"

    result = await call_with_resilience(flaky, breaker=breaker, max_retries=2, backoff_base_s=0.01)
    assert result == "ok na segunda tentativa"
    assert attempts["count"] == 2


async def test_timeout_counts_as_failure():
    breaker = CircuitBreaker(failure_threshold=1, recovery_s=60)

    async def hangs():
        await asyncio.sleep(10)

    with pytest.raises(asyncio.TimeoutError):
        await call_with_resilience(hangs, breaker=breaker, timeout_s=0.01, max_retries=0)
    assert breaker.is_open is True
