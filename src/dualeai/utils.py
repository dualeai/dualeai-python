"""Utility functions and classes for the Duale AI SDK."""

import hashlib

from dualeai.constants import DisplayLimits


def tenant_id_from_token(token: str) -> str:
    """Derive the logical tenant id from an API token (RFC-051).

    One definition so the OpenTelemetry ``tenant.id`` attribute and the cache
    backend namespace stay byte-identical — deriving them separately risks
    telemetry silently drifting from the cache isolation boundary.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def get_exception_type_name(exception: BaseException) -> str:
    """Get exception type name for logging."""
    return type(exception).__name__


def get_type_name(obj: object) -> str:
    """Get object type name for logging."""
    return type(obj).__name__ if obj is not None else "None"


def truncate_error_preview(error: str) -> str:
    """Truncate error string for logging preview.

    Example:
    --------
    >>> stack_trace = "Traceback (most recent call last):\\n" + "  File..." * 50
    >>> preview = truncate_error_preview(stack_trace)
    >>> len(preview) <= 200  # Keeps logs readable
    True

    Preserves the most important part of error messages (usually the beginning)
    while preventing stack traces from overwhelming log files.
    """
    return error[: DisplayLimits.ERROR_PREVIEW_LENGTH] if len(error) > DisplayLimits.ERROR_PREVIEW_LENGTH else error
