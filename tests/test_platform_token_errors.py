"""Public SDK error recovery for initial issuance and mandatory renewal.

Only issuer HTTP replies and time are controlled. Real credential ownership,
transport setup, and SDK error translation must refuse the Library operation
before discovery or a protected request can start.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import aiohttp
import pytest
from aioresponses import CallbackResult, aioresponses
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from dualeai import DualeAIAuthError, DualeAIConfig, DualeAIConnectionError, DualeAISDK
from dualeai import _platform_token as token_module
from dualeai.events.http_transport import HTTPTransport
from dualeai.models.problem_details import ProblemDetails

pytestmark = pytest.mark.unit

_TOKEN = "dualeai_synthetic_credential_not_for_service_error_messages"
_ENDPOINT = "https://api.example.test"
_ISSUER = f"{_ENDPOINT}/profile/platform-token"
_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _issued_payload(valid_until: datetime) -> dict[str, str]:
    return {
        "psk_id": "ab" * 64,
        "issuer_public_key": X25519PrivateKey.generate().public_key().public_bytes_raw().hex(),
        "identity": "agent:test-agent",
        "tenant_id": "tenant-test",
        "assurance_level": "aal2",
        "valid_until": valid_until.isoformat(),
    }


@pytest.fixture(params=["initial", "mandatory-renewal"])
async def issuance_sdk(request, monkeypatch):
    """Keep real transport ownership while entering either issuance state."""
    now = _NOW
    monkeypatch.setattr(token_module, "_now", lambda: now)
    config = DualeAIConfig(endpoint=_ENDPOINT, token=_TOKEN, tenant_id="tenant-test")
    transport = HTTPTransport(_ENDPOINT, _TOKEN, "tenant-test")
    renewing = request.param == "mandatory-renewal"
    sdk = DualeAISDK(config=config, transport=transport if renewing else None, auto_start=False)
    with aioresponses() as issuer:
        if renewing:
            public_key = X25519PrivateKey.generate().public_key().public_bytes_raw().hex()
            issuer.post(
                _ISSUER,
                status=201,
                payload={
                    "psk_id": "ab" * 64,
                    "issuer_public_key": public_key,
                    "identity": "agent:test-agent",
                    "tenant_id": "tenant-test",
                    "assurance_level": "aal2",
                    "valid_until": (_NOW + timedelta(minutes=5)).isoformat(),
                },
            )
            await transport.connect()
            now += timedelta(minutes=6)
        try:
            yield sdk, issuer, 2 if renewing else 1, now
        finally:
            await sdk.cleanup()
            await transport.disconnect()


@pytest.mark.parametrize(
    ("status", "error_code", "error_type"),
    [
        (401, "AUTHENTICATION_FAILED", DualeAIAuthError),
        (403, "AUTHORIZATION_FAILED", DualeAIAuthError),
        (429, "RATE_LIMIT_EXCEEDED", DualeAIConnectionError),
        (503, "SERVICE_UNAVAILABLE", DualeAIConnectionError),
    ],
)
async def test_issuance_errors_preserve_public_type_and_problem_details(
    issuance_sdk, status, error_code, error_type
) -> None:
    sdk, issuer, expected_calls, _now = issuance_sdk
    problem = {
        "title": "Synthetic issuer refusal",
        "status": status,
        "detail": "Contact the owner before retrying.",
        "error_code": error_code,
        "retryable": False,
        "owner_action_required": True,
    }
    issuer.post(_ISSUER, status=status, payload=problem)

    with pytest.raises(error_type) as raised:
        await sdk.libraries.list()

    assert raised.value.problem_details == ProblemDetails.model_validate(problem)
    assert "Contact the owner" not in str(raised.value)
    assert _TOKEN not in str(raised.value)
    assert sum(len(calls) for calls in issuer.requests.values()) == expected_calls
    assert {str(url) for _, url in issuer.requests} == {_ISSUER}


@pytest.mark.parametrize("status", [401, 503])
async def test_unstructured_issuer_body_is_not_exposed_in_public_error(issuance_sdk, status) -> None:
    sdk, issuer, expected_calls, _now = issuance_sdk
    issuer.post(_ISSUER, status=status, body=f"upstream echoed {_TOKEN}")

    with pytest.raises(DualeAIAuthError if status == 401 else DualeAIConnectionError) as raised:
        await sdk.libraries.list()

    assert raised.value.problem_details is None
    assert _TOKEN not in str(raised.value)
    assert sum(len(calls) for calls in issuer.requests.values()) == expected_calls


async def test_issuer_network_failure_is_a_public_connection_error(issuance_sdk) -> None:
    sdk, issuer, expected_calls, _now = issuance_sdk
    issuer.post(_ISSUER, exception=aiohttp.ClientConnectionError("synthetic network failure"))

    with pytest.raises(DualeAIConnectionError) as raised:
        await sdk.libraries.list()

    assert raised.value.problem_details is None
    assert sum(len(calls) for calls in issuer.requests.values()) == expected_calls


async def test_unusable_issued_token_is_a_public_connection_error(issuance_sdk) -> None:
    sdk, issuer, expected_calls, _now = issuance_sdk
    issuer.post(_ISSUER, status=201, payload={"unexpected": "invalid issued token"})

    with pytest.raises(DualeAIConnectionError) as raised:
        await sdk.libraries.list()

    assert raised.value.problem_details is None
    assert sum(len(calls) for calls in issuer.requests.values()) == expected_calls


@pytest.mark.parametrize("remaining_seconds", [0, -1])
async def test_expired_success_is_refused_before_discovery(issuance_sdk, remaining_seconds: int) -> None:
    sdk, issuer, expected_calls, now = issuance_sdk
    issuer.post(_ISSUER, status=201, payload=_issued_payload(now + timedelta(seconds=remaining_seconds)))

    with pytest.raises(DualeAIConnectionError) as raised:
        await sdk.libraries.list()

    assert raised.value.problem_details is None
    assert _TOKEN not in str(raised.value)
    assert sum(len(calls) for calls in issuer.requests.values()) == expected_calls
    assert {str(url) for _, url in issuer.requests} == {_ISSUER}


async def test_a_success_that_expires_while_issuance_is_pending_is_not_dispatched(monkeypatch) -> None:
    now = _NOW
    monkeypatch.setattr(token_module, "_now", lambda: now)
    entered = asyncio.Event()
    release = asyncio.Event()
    payload = _issued_payload(_NOW + timedelta(seconds=60))

    async def respond(_url, **_kwargs):
        entered.set()
        await release.wait()
        return CallbackResult(status=201, payload=payload)

    sdk = DualeAISDK(config=DualeAIConfig(endpoint=_ENDPOINT, token=_TOKEN, tenant_id="tenant-test"), auto_start=False)
    with aioresponses() as issuer:
        issuer.post(_ISSUER, callback=respond)
        operation = asyncio.create_task(sdk.libraries.list())
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            now = _NOW + timedelta(seconds=60)
            release.set()
            with pytest.raises(DualeAIConnectionError) as raised:
                await operation
            assert raised.value.problem_details is None
            assert _TOKEN not in str(raised.value)
            assert issuer.requests is not None
            assert sum(len(calls) for calls in issuer.requests.values()) == 1
            assert {str(url) for _, url in issuer.requests} == {_ISSUER}
        finally:
            release.set()
            await asyncio.gather(operation, return_exceptions=True)
            await sdk.cleanup()


async def test_expired_cover_preserves_the_issuer_error_without_protected_dispatch(monkeypatch) -> None:
    now = _NOW
    monkeypatch.setattr(token_module, "_now", lambda: now)
    entered = asyncio.Event()
    release = asyncio.Event()
    problem = {
        "title": "Synthetic issuer outage",
        "status": 503,
        "detail": "Synthetic temporary outage.",
        "error_code": "SERVICE_UNAVAILABLE",
        "retryable": True,
    }

    async def respond(_url, **_kwargs):
        entered.set()
        await release.wait()
        return CallbackResult(status=503, payload=problem)

    transport = HTTPTransport(_ENDPOINT, _TOKEN, "tenant-test")
    sdk = DualeAISDK(
        config=DualeAIConfig(endpoint=_ENDPOINT, token=_TOKEN, tenant_id="tenant-test"),
        transport=transport,
        auto_start=False,
    )
    with aioresponses() as issuer:
        issuer.post(_ISSUER, status=201, payload=_issued_payload(_NOW + timedelta(seconds=100)))
        await transport.connect()
        now = _NOW + timedelta(seconds=82)
        issuer.post(_ISSUER, callback=respond)
        operation = asyncio.create_task(sdk.libraries.list())
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            now = _NOW + timedelta(seconds=100)
            release.set()
            with pytest.raises(DualeAIConnectionError) as raised:
                await operation
            assert raised.value.problem_details == ProblemDetails.model_validate(problem)
            assert _TOKEN not in str(raised.value)
            assert issuer.requests is not None
            assert sum(len(calls) for calls in issuer.requests.values()) == 2
            assert {str(url) for _, url in issuer.requests} == {_ISSUER}
        finally:
            release.set()
            await asyncio.gather(operation, return_exceptions=True)
            await sdk.cleanup()
            await transport.disconnect()
