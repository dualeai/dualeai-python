"""Unit tests for ToolContext deadline helpers.

``remaining_seconds`` and ``is_expiring`` are the cooperative-cancellation API
the module and ``@tool`` docstrings tell callables to use. Deadlines are chosen
far from ``now`` (or clearly in the past) so the assertions never race the wall
clock.
"""

from datetime import datetime, timedelta, timezone

import pytest

from dualeai.tool_context import _CURRENT_TOOL_CONTEXT, ToolContext


def _context(deadline_at: datetime) -> ToolContext:
    return ToolContext(
        tool_call_id="call-1",
        task_id="task-1",
        attempt=1,
        deadline_at=deadline_at,
    )


@pytest.mark.unit
class TestUnitToolContextDeadline:
    """Deadline math for cooperative tool cancellation."""

    def test_remaining_seconds_positive_for_future_deadline(self):
        ctx = _context(datetime.now(timezone.utc) + timedelta(seconds=3600))
        remaining = ctx.remaining_seconds()
        # An hour of headroom: comfortably in the (3500, 3600] band even on a slow box.
        assert 3500 < remaining <= 3600

    def test_context_var_uses_dualeai_namespace(self):
        """The runtime context identifier uses the company namespace."""
        assert _CURRENT_TOOL_CONTEXT.name == "dualeai_current_tool_context"

    def test_remaining_seconds_never_negative_for_past_deadline(self):
        ctx = _context(datetime.now(timezone.utc) - timedelta(seconds=30))
        assert ctx.remaining_seconds() == 0.0

    def test_is_expiring_false_with_ample_time(self):
        ctx = _context(datetime.now(timezone.utc) + timedelta(seconds=3600))
        assert ctx.is_expiring() is False

    def test_is_expiring_true_when_deadline_passed(self):
        ctx = _context(datetime.now(timezone.utc) - timedelta(seconds=1))
        assert ctx.is_expiring() is True

    def test_is_expiring_threshold_is_honored(self):
        # 3600s remain; a 4000s window means "expiring", a 100s window does not.
        ctx = _context(datetime.now(timezone.utc) + timedelta(seconds=3600))
        assert ctx.is_expiring(within_seconds=4000) is True
        assert ctx.is_expiring(within_seconds=100) is False
