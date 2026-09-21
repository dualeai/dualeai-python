"""Tests for utility functions.

Covers ``get_exception_type_name``, ``get_type_name``, and the
``truncate_*`` helpers used for log preview formatting.

(``ConsecutiveTimeoutTracker`` was removed alongside its
zero-production-callers cleanup; tests for it lived here previously.)
"""

import pytest

from dualeai.constants import DisplayLimits
from dualeai.utils import (
    get_exception_type_name,
    get_type_name,
    truncate_error_preview,
)

ERROR_PREVIEW_LENGTH = DisplayLimits.ERROR_PREVIEW_LENGTH


@pytest.mark.unit
class TestUtilityFunctions:
    """Test utility functions for type names and string processing."""

    def test_get_exception_type_name(self):
        """Standard and built-in exceptions return their type name."""
        assert get_exception_type_name(ValueError("test")) == "ValueError"
        assert get_exception_type_name(RuntimeError("error")) == "RuntimeError"
        assert get_exception_type_name(TypeError("type")) == "TypeError"
        assert get_exception_type_name(KeyError("key")) == "KeyError"
        assert get_exception_type_name(Exception("base")) == "Exception"

    def test_get_type_name_basic(self):
        """get_type_name returns the type name string."""
        assert get_type_name("hello") == "str"
        assert get_type_name(42) == "int"
        assert get_type_name(3.14) == "float"
        assert get_type_name(True) == "bool"  # noqa: FBT003
        assert get_type_name([]) == "list"
        assert get_type_name({}) == "dict"
        assert get_type_name(()) == "tuple"
        assert get_type_name(set()) == "set"

    def test_get_type_name_none(self):
        """None is reported as the literal string 'None'."""
        assert get_type_name(None) == "None"

    def test_get_type_name_complex(self):
        """Complex object types report meaningful type names."""

        def lambda_func(x: int) -> int:
            return x

        assert get_type_name(lambda_func) == "function"

        generator = (x for x in range(5))
        assert get_type_name(generator) == "generator"

        assert get_type_name(len) == "builtin_function_or_method"

    def test_truncate_error_preview(self):
        """Long errors keep their head, exactly at the limit; short pass through."""
        long_error = "Traceback (most recent call last):\n" + ("  File..." * 50)
        truncated = truncate_error_preview(long_error)
        # Head-preserving hard cut — an empty or tail-only preview must fail.
        assert truncated == long_error[:ERROR_PREVIEW_LENGTH]

        short_error = "boom"
        assert truncate_error_preview(short_error) == "boom"

    def test_truncate_error_preview_special_characters(self):
        """Error truncation handles control chars without error."""
        error_with_control = "Error\x00\x01\x02" * 50
        assert truncate_error_preview(error_with_control) == error_with_control[:ERROR_PREVIEW_LENGTH]
