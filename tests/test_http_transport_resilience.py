"""Unit tests for HTTP transport mid-stream-disconnect resilience.

Long-running tasks routinely outlive the lifetime of the underlying
TCP connection between the SDK and the bridge — cloud LBs and proxies
close in-flight connections during backend reconciliation, and the SDK
needs to recover without losing the task's result.

This file covers the structural invariants of the resilience design:

- ``ClientPayloadError`` (chunked transfer cut mid-stream) must be in
  the retry tuple (it is NOT a subclass of ``ClientConnectionError``
  — verified explicitly).
- The POST→GET method switch on retry, encoded as the pure-function
  helper ``_resolve_retry_method``, must obey the bridge invariant
  documented in ``HTTPTransport._stream_request``.
- The retry budget must be sized to cover a typical backend
  re-registration window.

Behavioural integration tests of the full retry-with-Last-Event-ID
flow through ``pytest-aiohttp`` are tracked as a follow-up.
"""

import aiohttp
import pytest

from dualeai.events.http_transport import HTTPTransport, _resolve_retry_method


@pytest.mark.unit
class TestResolveRetryMethod:
    """Pure-function tests for the POST→GET retry decision rule.

    Encodes the bridge invariant from
    ``HTTPTransport._stream_request``: once the SDK got 2xx on the
    original POST, the bridge has already published the task to its
    message bus. Subsequent retries must use GET (read-only handler)
    to avoid duplicate publishes.
    """

    def test_post_with_established_stream_switches_to_get(self) -> None:
        """The core bridge invariant: post-2xx retry MUST use GET."""
        method, body = _resolve_retry_method("POST", {"action": "do something"}, stream_established=True)
        assert method == "GET"
        assert body is None  # GET endpoint takes no body

    def test_post_pre_flight_keeps_post_and_body(self) -> None:
        """Pre-flight failures retry as POST — bridge never saw the request."""
        method, body = _resolve_retry_method("POST", {"action": "do something"}, stream_established=False)
        assert method == "POST"
        assert body == {"action": "do something"}

    def test_get_unchanged_when_established(self) -> None:
        """Original GET requests stay GET on retry."""
        method, body = _resolve_retry_method("GET", None, stream_established=True)
        assert method == "GET"
        assert body is None

    def test_get_unchanged_when_not_established(self) -> None:
        """Original GET requests stay GET on pre-flight retry too."""
        method, body = _resolve_retry_method("GET", None, stream_established=False)
        assert method == "GET"
        assert body is None


@pytest.mark.unit
class TestRetryFilterIncludesStreamFailures:
    """The SDK's actual retry tuple covers the mid-stream failure modes.

    Imports ``RETRYABLE_STREAM_ERRORS`` (the tuple ``_stream_request``
    passes to tenacity) and asserts membership — deleting an entry from
    the production tuple fails here, not just a hierarchy assumption.
    """

    def test_client_payload_error_listed_explicitly(self) -> None:
        """``ClientPayloadError`` must be a MEMBER of the retry tuple.

        It is a SIBLING of ``ClientConnectionError`` under ``ClientError``,
        not a subclass — inheritance cannot cover it, so only explicit
        membership keeps chunked-transfer mid-stream cuts retried.
        """
        from dualeai.events.http_transport import RETRYABLE_STREAM_ERRORS

        assert aiohttp.ClientPayloadError in RETRYABLE_STREAM_ERRORS
        err = aiohttp.ClientPayloadError("simulated mid-stream cut")
        assert not isinstance(err, aiohttp.ClientConnectionError)

    def test_retry_tuple_covers_disconnect_timeout_and_checksum(self) -> None:
        """Server disconnects, timeouts, and checksum mismatches all retry."""
        from dualeai.events.http_transport import RETRYABLE_STREAM_ERRORS
        from dualeai.events.sse_parser import SSEChecksumError

        assert aiohttp.ClientConnectionError in RETRYABLE_STREAM_ERRORS
        assert aiohttp.ServerTimeoutError in RETRYABLE_STREAM_ERRORS
        assert aiohttp.ServerDisconnectedError in RETRYABLE_STREAM_ERRORS
        assert SSEChecksumError in RETRYABLE_STREAM_ERRORS


@pytest.mark.unit
class TestRetryConstants:
    """Guard against regressions on the retry-budget sizing."""

    def test_max_retries_sized_for_backend_reconciliation(self) -> None:
        """Budget must cover the typical backend re-registration window.

        Rationale lives in the ``HTTPTransport._stream_request``
        docstring: cloud LBs and proxies typically take a few tens of
        seconds to add and stabilize a new backend target, so the
        cumulative retry backoff needs to be at least that long. Ten
        attempts of ``wait_exponential_jitter(1, 10)`` give ~75-85 s.
        If you reduce this below ~5, read the docstring first.
        """
        assert HTTPTransport._MAX_RETRIES >= 10
