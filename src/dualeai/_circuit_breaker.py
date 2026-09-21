"""Small process-local circuit breaker for the public SDK."""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from enum import Enum
from typing import Literal


class _Phase(str, Enum):
    closed = "closed"
    open = "open"
    half_open = "half_open"


class _CircuitOpenError(RuntimeError):
    """Reject one SDK task before its backend operation starts."""

    def __init__(self, retry_after: float) -> None:
        super().__init__("Task execution circuit breaker is open")
        self.retry_after = max(0.0, retry_after)


class _Attempt:
    """Classify one admitted task without exposing mutable breaker state."""

    def __init__(self, generation: int) -> None:
        self.generation = generation
        self.outcome: Literal["failure", "ignored"] | None = None

    def fail(self) -> None:
        """Mark a terminal backend failure."""
        if self.outcome is None:
            self.outcome = "failure"

    def ignore(self) -> None:
        """Keep a local, caller, or business outcome out of dependency health."""
        if self.outcome is None:
            self.outcome = "ignored"

    def ignore_cancellation(self) -> None:
        """Cancellation is always neutral, even after an earlier mark."""
        self.outcome = "ignored"


class _CircuitBreaker:
    """Consecutive-failure breaker with one bounded recovery probe."""

    def __init__(
        self,
        *,
        failures: int,
        recovery_timeout: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failures < 1:
            raise ValueError("failures must be positive")
        if recovery_timeout <= 0:
            raise ValueError("recovery_timeout must be positive")
        self._failure_threshold = failures
        self._recovery_timeout = recovery_timeout
        self._clock = clock
        self._lock = asyncio.Lock()
        self._phase = _Phase.closed
        self._generation = 0
        self._consecutive_failures = 0
        self._open_until = 0.0
        self._probe_until = 0.0

    @asynccontextmanager
    async def protect(self) -> AsyncIterator[_Attempt]:
        """Admit one task and record its terminal outcome."""
        attempt = await self._admit()
        try:
            yield attempt
        except asyncio.CancelledError:
            attempt.ignore_cancellation()
            await self._complete(attempt, raised=False)
            raise
        except Exception:
            await self._complete(attempt, raised=True)
            raise
        except BaseException:
            attempt.ignore()
            await self._complete(attempt, raised=False)
            raise
        else:
            await self._complete(attempt, raised=False)

    async def _admit(self) -> _Attempt:
        async with self._lock:
            now = self._clock()
            if self._phase is _Phase.closed:
                return _Attempt(self._generation)
            if self._phase is _Phase.open:
                if now < self._open_until:
                    raise _CircuitOpenError(self._open_until - now)
                return self._reserve_probe(now)
            if now < self._probe_until:
                raise _CircuitOpenError(self._probe_until - now)
            return self._reserve_probe(now)

    def _reserve_probe(self, now: float) -> _Attempt:
        self._phase = _Phase.half_open
        self._generation += 1
        self._probe_until = now + self._recovery_timeout
        return _Attempt(self._generation)

    async def _complete(self, attempt: _Attempt, *, raised: bool) -> None:
        async with self._lock:
            if attempt.generation != self._generation:
                return
            if attempt.outcome == "ignored":
                self._record_ignored_probe()
                return
            failed = attempt.outcome == "failure" or raised
            if self._phase is _Phase.half_open:
                if failed:
                    self._open()
                else:
                    self._close()
                return
            if failed:
                self._consecutive_failures += 1
                if self._consecutive_failures >= self._failure_threshold:
                    self._open()
            else:
                self._consecutive_failures = 0

    def _record_ignored_probe(self) -> None:
        if self._phase is _Phase.half_open:
            self._phase = _Phase.open
            self._open_until = self._clock()
            self._probe_until = 0.0
            self._generation += 1

    def _open(self) -> None:
        self._phase = _Phase.open
        self._open_until = self._clock() + self._recovery_timeout
        self._probe_until = 0.0
        self._generation += 1

    def _close(self) -> None:
        self._phase = _Phase.closed
        self._consecutive_failures = 0
        self._open_until = 0.0
        self._probe_until = 0.0
        self._generation += 1
