"""Logging render tests.

Regression guard for the double-traceback defect: structlog's stdlib
``BoundLogger`` leaves ``exc_info`` on the emitted ``LogRecord``, so the stdlib
``Formatter`` re-renders the traceback on top of the structlog renderer's own —
one ``logger.exception`` printed the traceback twice. ``ProcessorFormatter``
moves rendering onto the handler and nulls ``record.exc_info``, so it renders
once.
"""

import logging

import pytest
import structlog

from dualeai.logging_config import configure_logging

# Plain header line emitted once per rendered traceback by the stdlib/plain
# formatter. Rich's panel header carries ANSI + no trailing colon, so this exact
# literal isolates each real traceback render regardless of whether rich is
# installed (it is, via the dev extra) — the count equals the number of renders.
_PLAIN_TRACEBACK_HEADER = "Traceback (most recent call last):"


@pytest.mark.unit
class TestLoggingExceptionRender:
    def test_tool_error_traceback_renders_once(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One ``logger.exception`` renders the traceback exactly once.

        Force plain exception formatting so the count is deterministic (rich's
        panel would otherwise leak the payload into a locals block). The bug is
        the *second* render, independent of formatter style.
        """
        real_console = structlog.dev.ConsoleRenderer

        def _plain_console(*, colors: bool = True) -> structlog.dev.ConsoleRenderer:
            # configure_logging constructs ConsoleRenderer(colors=...); force plain
            # tracebacks so the render count is deterministic (rich would spread the
            # payload across a locals panel).
            return real_console(colors=colors, exception_formatter=structlog.dev.plain_traceback)

        # This seam works because configure_logging references
        # structlog.dev.ConsoleRenderer by attribute at call time; a
        # ``from structlog.dev import ConsoleRenderer`` in production would defeat it.
        monkeypatch.setattr(structlog.dev, "ConsoleRenderer", _plain_console)

        configure_logging(level=logging.ERROR)
        # Fresh logger name: cache_logger_on_first_use bakes the resolved logger,
        # so a name not used by earlier tests picks up this reconfigure.
        log = structlog.get_logger("test_logging_render")

        try:
            raise ValueError("boom-rc2-marker")
        except ValueError:
            log.exception("tool execution failed", tool="adjust_price")

        out = capsys.readouterr().out
        assert "boom-rc2-marker" in out, "exception was not logged at all"
        assert out.count(_PLAIN_TRACEBACK_HEADER) == 1, (
            f"expected exactly one traceback render, got {out.count(_PLAIN_TRACEBACK_HEADER)}"
        )
