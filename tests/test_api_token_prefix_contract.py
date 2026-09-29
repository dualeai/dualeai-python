"""The token the platform mints is the token this SDK accepts.

NEITHER SIDE CAN IMPORT THE OTHER. This package ships to PyPI and cannot import
platform code, so the prefix is pinned separately on each side. That is what makes a
drift silent: the platform mints one prefix, this SDK refuses it, and nothing fails
until a customer's first request.

The oracle is the hand-written literal below, transcribed from the platform's pin
rather than imported, so the two pins disagree here the moment either one moves.
"""

import pytest
from pydantic import ValidationError

from dualeai.config import DualeAIConfig

pytestmark = pytest.mark.unit

# Byte-for-byte the platform's ``PINNED_TOKEN``.
PINNED_TOKEN = "dualeai_contract-pinned-token-padded-past-the-32-byte-psk-floor"


def test_a_platform_minted_token_is_accepted() -> None:
    """The prefix check admits the shape the platform actually mints."""
    assert DualeAIConfig(token=PINNED_TOKEN).token == PINNED_TOKEN


@pytest.mark.parametrize(
    "token",
    [
        "duale_contract-pinned-token-padded-past-the-32-byte-psk-floor",
        "contract-pinned-token-padded-past-the-32-byte-psk-floor",
        "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.sig",
    ],
    ids=["package-prefix", "unprefixed", "jwt-shaped"],
)
def test_a_token_without_the_wire_prefix_is_refused(token: str) -> None:
    """``duale_`` is the Python package prefix, and it is not a credential prefix."""
    with pytest.raises(ValidationError):
        DualeAIConfig(token=token)
