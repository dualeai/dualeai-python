"""Focused tests for the SDK-local task circuit breaker."""

import asyncio

import pytest

from dualeai._circuit_breaker import _CircuitBreaker, _CircuitOpenError


class Clock:
    """Small monotonic clock controlled by each test."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.unit
class TestSDKCircuitBreaker:
    """Exercise the state transitions that protect public SDK tasks."""

    @pytest.mark.parametrize(
        ("failures", "recovery_timeout", "message"),
        [(0, 10.0, "failures must be positive"), (1, 0.0, "recovery_timeout must be positive")],
    )
    def test_invalid_configuration_is_rejected(
        self,
        failures: int,
        recovery_timeout: float,
        message: str,
    ) -> None:
        with pytest.raises(ValueError, match=message):
            _CircuitBreaker(failures=failures, recovery_timeout=recovery_timeout)

    async def test_consecutive_failures_open_and_success_resets(self) -> None:
        clock = Clock()
        breaker = _CircuitBreaker(failures=2, recovery_timeout=10.0, clock=clock)

        async with breaker.protect() as attempt:
            attempt.fail()
        async with breaker.protect():
            pass
        async with breaker.protect() as attempt:
            attempt.fail()
        async with breaker.protect() as attempt:
            attempt.fail()

        with pytest.raises(_CircuitOpenError, match="circuit breaker is open"):
            async with breaker.protect():
                pass

    async def test_raised_error_counts_unless_ignored(self) -> None:
        breaker = _CircuitBreaker(failures=1, recovery_timeout=10.0)

        with pytest.raises(ValueError, match="local"):
            async with breaker.protect() as attempt:
                attempt.ignore()
                raise ValueError("local")
        async with breaker.protect():
            pass

        with pytest.raises(ValueError, match="dependency"):
            async with breaker.protect():
                raise ValueError("dependency")
        with pytest.raises(_CircuitOpenError):
            async with breaker.protect():
                pass

    async def test_only_one_recovery_probe_runs(self) -> None:
        clock = Clock()
        breaker = _CircuitBreaker(failures=1, recovery_timeout=10.0, clock=clock)
        async with breaker.protect() as attempt:
            attempt.fail()
        clock.advance(10.0)

        probe = breaker.protect()
        await probe.__aenter__()
        with pytest.raises(_CircuitOpenError):
            async with breaker.protect():
                pass
        await probe.__aexit__(None, None, None)

        async with breaker.protect():
            pass

    async def test_failed_probe_reopens_for_full_interval(self) -> None:
        clock = Clock()
        breaker = _CircuitBreaker(failures=1, recovery_timeout=10.0, clock=clock)
        async with breaker.protect() as attempt:
            attempt.fail()
        clock.advance(10.0)
        async with breaker.protect() as attempt:
            attempt.fail()

        with pytest.raises(_CircuitOpenError) as raised:
            async with breaker.protect():
                pass
        assert raised.value.retry_after == 10.0

    async def test_ignored_probe_allows_an_immediate_replacement(self) -> None:
        clock = Clock()
        breaker = _CircuitBreaker(failures=1, recovery_timeout=10.0, clock=clock)
        async with breaker.protect() as attempt:
            attempt.fail()
        clock.advance(10.0)

        async with breaker.protect() as attempt:
            attempt.ignore()

        async with breaker.protect():
            pass
        async with breaker.protect():
            pass

    async def test_expired_probe_replaces_stale_attempt(self) -> None:
        clock = Clock()
        breaker = _CircuitBreaker(failures=1, recovery_timeout=10.0, clock=clock)
        async with breaker.protect() as attempt:
            attempt.fail()
        clock.advance(10.0)

        stale_probe = breaker.protect()
        await stale_probe.__aenter__()
        clock.advance(10.0)
        current_probe = breaker.protect()
        current_attempt = await current_probe.__aenter__()
        await stale_probe.__aexit__(None, None, None)

        with pytest.raises(_CircuitOpenError):
            async with breaker.protect():
                pass
        current_attempt.fail()
        await current_probe.__aexit__(None, None, None)

    async def test_cancellation_is_neutral(self) -> None:
        breaker = _CircuitBreaker(failures=1, recovery_timeout=10.0)
        entered = asyncio.Event()

        async def wait_forever() -> None:
            async with breaker.protect():
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(wait_forever())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        async with breaker.protect():
            pass

    async def test_cancelled_probe_allows_an_immediate_replacement(self) -> None:
        clock = Clock()
        breaker = _CircuitBreaker(failures=1, recovery_timeout=10.0, clock=clock)
        async with breaker.protect() as attempt:
            attempt.fail()
        clock.advance(10.0)

        entered = asyncio.Event()

        async def hold_probe() -> None:
            async with breaker.protect():
                entered.set()
                await asyncio.Event().wait()

        probe = asyncio.create_task(hold_probe())
        await entered.wait()
        probe.cancel()
        with pytest.raises(asyncio.CancelledError):
            await probe

        async with breaker.protect():
            pass
        async with breaker.protect():
            pass

    async def test_late_closed_result_cannot_update_new_open_generation(self) -> None:
        breaker = _CircuitBreaker(failures=1, recovery_timeout=10.0)
        first = breaker.protect()
        second = breaker.protect()
        first_attempt = await first.__aenter__()
        await second.__aenter__()

        first_attempt.fail()
        await first.__aexit__(None, None, None)
        await second.__aexit__(None, None, None)

        with pytest.raises(_CircuitOpenError):
            async with breaker.protect():
                pass

    async def test_interpreter_control_exception_is_neutral(self) -> None:
        breaker = _CircuitBreaker(failures=1, recovery_timeout=10.0)

        with pytest.raises(SystemExit):
            async with breaker.protect():
                raise SystemExit(7)

        async with breaker.protect():
            pass
