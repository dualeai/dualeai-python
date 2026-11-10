"""Console logging renders each traceback once without exposing frame locals."""

import logging
from collections.abc import Iterator
from uuid import uuid4

import pytest
import structlog

from dualeai.logging_config import configure_logging

_PLAIN_TRACEBACK_HEADER = "Traceback (most recent call last):"


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers = root.handlers[:]
    root_level = root.level
    library_levels = {name: logging.getLogger(name).level for name in ("aiohttp", "aiojobs", "asyncio")}
    structlog_config = structlog.get_config()
    try:
        yield
    finally:
        root.handlers = handlers
        root.setLevel(root_level)
        for name, level in library_levels.items():
            logging.getLogger(name).setLevel(level)
        structlog.configure(**structlog_config)


@pytest.mark.unit
class TestLoggingExceptionRender:
    @pytest.mark.parametrize("logger_kind", ["structlog", "stdlib"])
    @pytest.mark.parametrize("level", [logging.ERROR, logging.DEBUG])
    def test_tracebacks_exclude_credentials_from_frame_locals(
        self, capsys: pytest.CaptureFixture[str], logger_kind: str, level: int
    ) -> None:
        """Console output keeps exception causes and frames without their secrets."""
        configure_logging(level=level)
        logger_name = f"test_logging_credentials_{logger_kind}_{level}"
        log = structlog.get_logger(logger_name) if logger_kind == "structlog" else logging.getLogger(logger_name)
        credential = uuid4().hex

        try:
            _raise_with_credential_in_frame(credential)
        except RuntimeError:
            log.exception("connection failed")

        out = capsys.readouterr().out
        assert credential not in out
        assert "ValueError: synthetic issuer refusal" in out
        assert "RuntimeError: synthetic connection failure" in out
        assert "_raise_with_credential_in_frame" in out
        assert "The above exception was the direct cause" in out

    def test_tool_error_traceback_renders_once(self, capsys: pytest.CaptureFixture[str]) -> None:
        """One ``logger.exception`` renders the traceback exactly once."""
        configure_logging(level=logging.ERROR)
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


def _raise_with_credential_in_frame(credential: str) -> None:
    try:
        raise ValueError("synthetic issuer refusal")
    except ValueError as cause:
        raise RuntimeError("synthetic connection failure") from cause
