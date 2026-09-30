"""The SDK derives the same pre-shared key the issuer recorded.

A GOLDEN VECTOR, not a round trip. Deriving twice in one process proves only
that the code agrees with itself; this implementation must agree with the
issuer's across a network boundary and across a release boundary, because the
SDK ships on its own cycle. A mismatch is silent — no tunnel opens and every
request is refused — so the shared value is what makes a divergence fail
loudly here.

The browser client pins the same vector. Three implementations, one number.
The expected value was independently calculated with Node's X25519 and
RFC 5869's HMAC-SHA-256 construction, using empty salt and the UTF-8 context
`dualeai-platform-token/1`.
"""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from dualeai._platform_token import API_TOKEN_PREFIX, derive_psk

pytestmark = pytest.mark.unit

_CLIENT_PRIVATE = bytes.fromhex("77" * 32)
_ISSUER_PUBLIC = "ce8d3ad1ccb633ec7b70c17814a5c76ecd029685050d344745ba05870e587d59"
_EXPECTED = "dualeai_6022dc966a28a0bcb0989f498d97424aa6fba22d096906b5aa50cc5a77fea81d"


def test_it_reaches_the_exact_key_the_issuer_recorded() -> None:
    private = X25519PrivateKey.from_private_bytes(_CLIENT_PRIVATE)
    actual = derive_psk(private, _ISSUER_PUBLIC)

    assert actual == _EXPECTED
    assert actual.startswith(API_TOKEN_PREFIX)


def test_two_clients_reach_two_keys() -> None:
    """The client's own ephemeral key is half of the secret."""
    first = derive_psk(X25519PrivateKey.generate(), _ISSUER_PUBLIC)
    second = derive_psk(X25519PrivateKey.generate(), _ISSUER_PUBLIC)

    assert first != second


def test_a_malformed_issuer_key_is_refused_rather_than_derived_against() -> None:
    with pytest.raises(ValueError):
        derive_psk(X25519PrivateKey.from_private_bytes(_CLIENT_PRIVATE), "ab" * 16)
