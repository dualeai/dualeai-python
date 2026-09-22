"""Utility functions for the Duale AI SDK.

Behavior is covered by ``tests/test_utils.py``.
"""

import hashlib

from dualeai.constants import DisplayLimits


def tenant_id_from_token(token: str) -> str:
    """Return the SHA-256 token fingerprint used as a local namespace.

    The compatibility name predates its current use. The result is not the
    configured or canonical tenant id. The cache backend and OpenTelemetry
    resource both use the same fingerprint; rotating the token changes it.
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
