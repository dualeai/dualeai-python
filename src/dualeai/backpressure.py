"""SDK task dependency health and activity scheduler limits.

This module implements:
1. Activity scheduler capacity configuration
2. Process-local task circuit breaking
3. Task lifecycle counters
"""

import time

from pydantic import BaseModel, ConfigDict, Field

from dualeai._circuit_breaker import _CircuitBreaker


class BackpressureMetrics(BaseModel):
    """Metrics for backpressure monitoring."""

    tasks_submitted: int = Field(default=0, description="Total tasks submitted")
    tasks_rejected: int = Field(default=0, description="Total tasks rejected due to backpressure")
    tasks_completed: int = Field(default=0, description="Total tasks completed successfully")
    tasks_failed: int = Field(default=0, description="Total tasks failed")

    last_rejection_time: float | None = Field(default=None, description="Timestamp of last circuit rejection")
    consecutive_rejections: int = Field(default=0, description="Consecutive circuit rejections")

    model_config = ConfigDict(validate_assignment=True, use_attribute_docstrings=True)

    def record_rejection(self) -> None:
        """Record a task rejection due to backpressure."""
        self.tasks_rejected += 1
        self.consecutive_rejections += 1
        self.last_rejection_time = time.time()

    def record_submission(self) -> None:
        """Record successful task submission."""
        self.tasks_submitted += 1
        self.consecutive_rejections = 0  # Reset on success


class BackpressureConfig(BaseModel):
    """Activity scheduler limits and task circuit-breaker policy."""

    max_concurrent_activities: int = Field(
        default=100,
        description="Maximum number of concurrently running cached activities",
        ge=1,
        le=10000,
    )
    max_pending_activities: int = Field(
        default=500,
        description="Maximum number of queued cached activities",
        ge=1,
        le=50000,
    )

    # Circuit breaker
    failure_threshold: int = Field(
        default=10,
        description="Consecutive failures to open circuit",
        ge=1,
        le=100,
        strict=True,
    )
    recovery_timeout_seconds: float = Field(
        default=30.0,
        description="Delay before one circuit-breaker recovery probe may be admitted",
        gt=0.0,
        le=300.0,
    )

    model_config = ConfigDict(validate_assignment=True, use_enum_values=True, use_attribute_docstrings=True)


class BackpressureController:
    """Track task outcomes and own the process-local dependency breaker."""

    def __init__(
        self,
        config: BackpressureConfig | None = None,
    ):
        """Initialize task dependency health state.

        Args:
            config: Activity limits and task circuit-breaker policy.
        """
        self.config = config or BackpressureConfig()
        self.metrics = BackpressureMetrics()
        self._task_breaker = _CircuitBreaker(
            failures=self.config.failure_threshold,
            recovery_timeout=self.config.recovery_timeout_seconds,
        )

    def record_failure(self) -> None:
        """Record a terminal task failure metric."""
        self.metrics.tasks_failed += 1

    def record_success(self) -> None:
        """Record a terminal task success metric."""
        self.metrics.tasks_completed += 1
