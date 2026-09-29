"""Public SDK error recovery for initial issuance and mandatory renewal.

Only issuer HTTP replies and time are controlled. Real credential ownership,
transport setup, and SDK error translation must refuse the Library operation
before discovery or a protected request can start.
"""

from datetime import datetime, timedelta, timezone

import aiohttp
import pytest
from aioresponses import aioresponses
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
            yield sdk, issuer, 2 if renewing else 1
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
    sdk, issuer, expected_calls = issuance_sdk
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
    sdk, issuer, expected_calls = issuance_sdk
    issuer.post(_ISSUER, status=status, body=f"upstream echoed {_TOKEN}")

    with pytest.raises(DualeAIAuthError if status == 401 else DualeAIConnectionError) as raised:
        await sdk.libraries.list()

    assert raised.value.problem_details is None
    assert _TOKEN not in str(raised.value)
    assert sum(len(calls) for calls in issuer.requests.values()) == expected_calls


async def test_issuer_network_failure_is_a_public_connection_error(issuance_sdk) -> None:
    sdk, issuer, expected_calls = issuance_sdk
    issuer.post(_ISSUER, exception=aiohttp.ClientConnectionError("synthetic network failure"))

    with pytest.raises(DualeAIConnectionError) as raised:
        await sdk.libraries.list()

    assert raised.value.problem_details is None
    assert sum(len(calls) for calls in issuer.requests.values()) == expected_calls


async def test_unusable_issued_token_is_a_public_connection_error(issuance_sdk) -> None:
    sdk, issuer, expected_calls = issuance_sdk
    issuer.post(_ISSUER, status=201, payload={"unexpected": "invalid issued token"})

    with pytest.raises(DualeAIConnectionError) as raised:
        await sdk.libraries.list()

    assert raised.value.problem_details is None
    assert sum(len(calls) for calls in issuer.requests.values()) == expected_calls
