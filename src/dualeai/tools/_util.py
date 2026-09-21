"""Small shared tool-pipeline utilities."""

from __future__ import annotations

import types
from collections.abc import Callable


def callable_name(func: Callable[..., object]) -> str:
    """Return a stable callable name for functions and callable instances."""
    return func.__name__ if isinstance(func, types.FunctionType) else type(func).__name__
