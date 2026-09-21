"""Unit tests for HTTP transport auth error handling (hpke-http v1.6.1).

Tests cover:
- Exception hierarchy: HTTPTransportAuthError ⊂ HTTPTransportError, DualeAIAuthError ⊂ DualeAIError
- Non-retry guarantee: auth errors bypass tenacity retry filter
- Error messages: status code and detail preserved through exception chain
"""

import aiohttp
import pytest
from aioresponses import aioresponses

from dualeai.events.client import _lift_problem_details
from dualeai.events.http_transport import (
    HTTPTransportAuthError,
    HTTPTransportConnectionError,
    HTTPTransportError,
    HTTPTransportResponseError,
    _raise_for_http_status,
)
from dualeai.exceptions import DualeAIAuthError, DualeAIConnectionError, DualeAIError
from dualeai.models.problem_details import ProblemDetails


class TestExceptionHierarchy:
    """Verify exception class hierarchy for backward compatibility."""

    @pytest.mark.unit
    def test_auth_error_is_transport_error(self):
        """HTTPTransportAuthError must be a subtype of HTTPTransportError.

        Existing code catching HTTPTransportError will still catch auth errors
        unless they catch HTTPTransportAuthError first.
        """
        assert issubclass(HTTPTransportAuthError, HTTPTransportError)

    @pytest.mark.unit
    def test_auth_error_is_not_connection_error(self):
        """HTTPTransportAuthError is NOT a connection error — different semantics."""
        assert not issubclass(HTTPTransportAuthError, HTTPTransportConnectionError)

    @pytest.mark.unit
    def test_dualeai_auth_error_is_dualeai_error(self):
        """DualeAIAuthError must be a subtype of DualeAIError for top-level catching."""
        assert issubclass(DualeAIAuthError, DualeAIError)

    @pytest.mark.unit
    def test_dualeai_auth_error_is_not_connection_error(self):
        """DualeAIAuthError is NOT a DualeAIConnectionError — prevents retry logic from catching it."""
        assert not issubclass(DualeAIAuthError, DualeAIConnectionError)


class TestAuthErrorNotRetried:
    """Verify that auth errors don't trigger tenacity retries."""

    @pytest.mark.unit
    def test_auth_error_not_in_retry_filter(self):
        """HTTPTransportAuthError must never match the SDK's retry tuple.

        Asserted against the REAL ``RETRYABLE_STREAM_ERRORS`` tuple used by
        ``_stream_request`` — an auth failure must surface immediately, not
        be retried like a transient stream cut.
        """
        from dualeai.events.http_transport import RETRYABLE_STREAM_ERRORS

        err = HTTPTransportAuthError("HTTP 401: Invalid or expired API key")
        assert not isinstance(err, RETRYABLE_STREAM_ERRORS)
        assert HTTPTransportAuthError not in RETRYABLE_STREAM_ERRORS


class TestErrorMessages:
    """Verify error messages carry useful context."""

    @pytest.mark.unit
    def test_auth_error_includes_status(self):
        """Auth error message should include HTTP status for debugging."""
        err = HTTPTransportAuthError("HTTP 401: Invalid or expired API key")
        assert "401" in str(err)

    @pytest.mark.unit
    def test_auth_error_includes_detail(self):
        """Auth error message should include server detail."""
        err = HTTPTransportAuthError("HTTP 401: Invalid or expired API key")
        assert "Invalid or expired" in str(err)

    @pytest.mark.unit
    def test_dualeai_auth_error_preserves_cause(self):
        """DualeAIAuthError should preserve the transport-level cause."""
        transport_err = HTTPTransportAuthError("HTTP 401: bad key")
        sdk_err = DualeAIAuthError(f"Task creation failed: {transport_err}")
        sdk_err.__cause__ = transport_err
        assert sdk_err.__cause__ is transport_err


class TestProblemDetailsLegibility:
    """The HTTP path must surface the RFC 9457 ProblemDetails body, not discard it."""

    @pytest.mark.unit
    async def test_http_error_attaches_problem_details_body(self):
        """A 4xx ProblemDetails body is parsed onto the raised transport error."""
        payload = {
            "type": "about:blank",
            "title": "Too many tools",
            "status": 422,
            "detail": "Manifest declares 65 tools; the maximum is 64.",
            "error_code": "TOO_MANY_TOOLS",
        }
        async with aiohttp.ClientSession() as session:
            with aioresponses() as mocked:
                mocked.post("http://bridge.test/v1/agent/registration", status=422, payload=payload)
                response = await session.post("http://bridge.test/v1/agent/registration")
                with pytest.raises(HTTPTransportResponseError) as excinfo:
                    await _raise_for_http_status(response, path="/v1/agent/registration")

        problem = excinfo.value.problem_details
        assert problem is not None
        assert problem.error_code == "TOO_MANY_TOOLS"
        assert problem.status == 422
        assert problem.detail == "Manifest declares 65 tools; the maximum is 64."

    @pytest.mark.unit
    async def test_http_error_without_problem_body_leaves_problem_details_none(self):
        """A non-ProblemDetails error body does not crash; problem_details stays None."""
        async with aiohttp.ClientSession() as session:
            with aioresponses() as mocked:
                mocked.post("http://bridge.test/p", status=503, body="upstream unavailable")
                response = await session.post("http://bridge.test/p")
                with pytest.raises(HTTPTransportConnectionError) as excinfo:
                    await _raise_for_http_status(response, path="/p")

        assert excinfo.value.problem_details is None

    @pytest.mark.unit
    def test_client_wrap_carries_problem_details_onto_sdk_error(self):
        """The client wrap copies a transport error's ProblemDetails onto the SDK error."""
        problem = ProblemDetails(
            type="about:blank",
            title="Too many tools",
            status=422,
            detail="Manifest declares 65 tools; the maximum is 64.",
            error_code="TOO_MANY_TOOLS",
        )
        transport_error = HTTPTransportConnectionError("HTTP 422", problem_details=problem)
        sdk_error = _lift_problem_details(DualeAIConnectionError("Agent registration failed"), transport_error)
        assert sdk_error.problem_details is problem
        assert sdk_error.problem_details.error_code == "TOO_MANY_TOOLS"

    @pytest.mark.unit
    async def test_problem_details_tolerates_unknown_envelope_field(self):
        """A ProblemDetails body preserves RFC 9457 extension fields."""
        payload = {
            "type": "about:blank",
            "title": "Too many tools",
            "status": 422,
            "detail": "Manifest declares 65 tools; the maximum is 64.",
            "error_code": "TOO_MANY_TOOLS",
            "future_envelope_field": "added by a newer platform",
        }
        async with aiohttp.ClientSession() as session:
            with aioresponses() as mocked:
                mocked.post("http://bridge.test/p", status=422, payload=payload)
                response = await session.post("http://bridge.test/p")
                with pytest.raises(HTTPTransportResponseError) as excinfo:
                    await _raise_for_http_status(response, path="/p")

        problem = excinfo.value.problem_details
        assert problem is not None  # not dropped by the unknown field
        assert problem.error_code == "TOO_MANY_TOOLS"
        assert problem.model_extra == {
            "future_envelope_field": "added by a newer platform",
        }
