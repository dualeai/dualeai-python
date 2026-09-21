"""Tests for backpressure functionality.

This module tests the backpressure classes to improve coverage.
"""

import pytest
from pydantic import ValidationError

from dualeai.backpressure import (
    BackpressureConfig,
    BackpressureController,
    BackpressureMetrics,
)


@pytest.mark.unit
class TestBackpressureConfig:
    """Test BackpressureConfig model."""

    def test_backpressure_config_defaults(self):
        """Test BackpressureConfig default values."""
        config = BackpressureConfig()

        # Pinned literals — a fat-fingered default must fail here.
        assert config.max_concurrent_activities == 100
        assert config.max_pending_activities == 500
        assert config.failure_threshold == 10
        assert config.recovery_timeout_seconds == 30.0

    def test_backpressure_config_custom_values(self):
        """Test BackpressureConfig with custom values."""
        config = BackpressureConfig(max_concurrent_activities=50, max_pending_activities=250)

        assert config.max_concurrent_activities == 50
        assert config.max_pending_activities == 250

    def test_failure_threshold_rejects_boolean(self):
        """A boolean must not become a one-failure public circuit."""
        with pytest.raises(ValidationError):
            BackpressureConfig(failure_threshold=True)


@pytest.mark.unit
class TestBackpressureMetrics:
    """Test BackpressureMetrics model."""

    def test_metrics_initialization(self):
        """Test BackpressureMetrics initialization."""
        metrics = BackpressureMetrics()

        assert metrics.tasks_submitted == 0
        assert metrics.tasks_rejected == 0
        assert metrics.tasks_completed == 0
        assert metrics.tasks_failed == 0

    def test_metrics_serialization(self):
        """Test BackpressureMetrics serialization."""
        metrics = BackpressureMetrics(tasks_submitted=50, tasks_completed=45)

        # Should be serializable to dict
        data = metrics.model_dump()
        assert isinstance(data, dict)
        assert data["tasks_submitted"] == 50
        assert data["tasks_completed"] == 45


@pytest.mark.unit
class TestBackpressureController:
    """Test BackpressureController functionality."""

    def test_controller_initialization(self):
        """The controller owns dependency health, not scheduler state."""
        config = BackpressureConfig(failure_threshold=3)
        controller = BackpressureController(config)

        assert controller.config == config
        assert isinstance(controller.metrics, BackpressureMetrics)

    def test_controller_records_terminal_outcomes(self):
        """Task lifecycle counters follow submission and terminal outcomes."""
        controller = BackpressureController()

        controller.metrics.record_submission()
        controller.record_success()
        controller.record_failure()

        assert controller.metrics.tasks_submitted == 1
        assert controller.metrics.tasks_completed == 1
        assert controller.metrics.tasks_failed == 1
