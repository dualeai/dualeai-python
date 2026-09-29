"""Issuance goes through the generated contract in both directions.

BUILDING THE REQUEST BY HAND AND READING THE ANSWER BY STRING KEY CARRIES NONE
OF THE CATALOGUE'S CONSTRAINTS: a `psk_id` of the wrong width is accepted, a
field the issuer renamed raises `KeyError` far from the cause, and nothing
refuses an unexpected one. These pin the boundary against a loopback issuer
rather than a stub, because the body that has to be refused is the one that
arrives over HTTP.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Final

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from pydantic import ValidationError

from dualeai._platform_token import derive_psk, issue

pytestmark = pytest.mark.unit

_API_TOKEN: Final[str] = "dualeai_" + "00" * 32
"""A synthetic credential. The loopback issuer never looks at it."""

_PSK_ID: Final[str] = "ab" * 64
"""128 lowercase hex characters, which is the whole of the identifier's shape."""

_ISSUER_PRIVATE: Final[X25519PrivateKey] = X25519PrivateKey.generate()
"""Kept, not discarded: the test derives the shared secret from the issuer's side."""

_ISSUER_PUBLIC: Final[str] = _ISSUER_PRIVATE.public_key().public_bytes_raw().hex()
"""A real public key, so the happy path completes an exchange instead of raising."""


def _answer(**overrides: object) -> dict[str, object]:
    """One well-formed answer, with the field under test replaced."""
    return {
        "psk_id": _PSK_ID,
        "issuer_public_key": _ISSUER_PUBLIC,
        "identity": "agent:agent-one",
        "tenant_id": "tenant-one",
        "assurance_level": "aal2",
        "valid_until": "2026-01-01T00:00:00Z",
        **overrides,
    }


@asynccontextmanager
async def _issuer(answer: object) -> AsyncIterator[tuple[str, list[dict[str, object]]]]:
    """A loopback issuer that returns ``answer`` and records what it was sent."""
    received: list[dict[str, object]] = []

    async def handle(request: web.Request) -> web.Response:
        received.append(await request.json())
        return web.json_response(answer, status=HTTPStatus.CREATED)

    app = web.Application()
    app.router.add_post("/profile/platform-token", handle)
    server = TestServer(app)
    await server.start_server()
    try:
        yield str(server.make_url("/")), received
    finally:
        await server.close()


async def test_a_well_formed_answer_becomes_a_usable_token() -> None:
    """The instant comes back aware, on every Python this package supports.

    The issuer stamps a trailing `Z`, which `datetime.fromisoformat` rejects on
    the 3.10 floor. Parsing through the generated model removes that split.
    """
    async with _issuer(_answer()) as (endpoint, _received), aiohttp.ClientSession() as session:
        token = await issue(session, endpoint, _API_TOKEN)

    assert token.psk_id == bytes.fromhex(_PSK_ID)
    assert token.valid_until == datetime(2026, 1, 1, tzinfo=timezone.utc)


async def test_the_key_it_keeps_is_the_private_half_of_the_one_it_published() -> None:
    """Both sides must reach the same secret, or every tunnel is refused at runtime.

    THE ORACLE IS ECDH'S SYMMETRY, not `derive_psk`'s arithmetic — that is pinned
    against four independent implementations in `tests/test_platform_token.py`.
    Here the issuer derives from its own private key and the `client_public_key`
    it actually received, so the two agree only if `issue` kept the private half
    of the key it published. Nothing else in this package observes `psk`:
    `issue` deriving against a throwaway key passed the whole unit suite.
    """
    async with _issuer(_answer()) as (endpoint, received), aiohttp.ClientSession() as session:
        token = await issue(session, endpoint, _API_TOKEN)

    published = received[0]["client_public_key"]
    assert isinstance(published, str)
    assert token.psk == derive_psk(_ISSUER_PRIVATE, published).encode("utf-8")


async def test_an_identifier_of_the_wrong_width_is_refused() -> None:
    """The silent case: 64 hex characters still parse as bytes and open nothing."""
    async with _issuer(_answer(psk_id="ab" * 32)) as (endpoint, _received), aiohttp.ClientSession() as session:
        with pytest.raises(ValidationError):
            await issue(session, endpoint, _API_TOKEN)


async def test_a_renamed_field_is_a_validation_error() -> None:
    """Not a `KeyError` three frames away from the contract that changed."""
    answer = _answer()
    answer["expires_at"] = answer.pop("valid_until")

    async with _issuer(answer) as (endpoint, _received), aiohttp.ClientSession() as session:
        with pytest.raises(ValidationError):
            await issue(session, endpoint, _API_TOKEN)
