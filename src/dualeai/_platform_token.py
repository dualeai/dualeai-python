"""Exchange an API token for the platform token every service accepts.

The SDK generates an ephemeral X25519 key pair and sends the public half with
its API token. The issuer returns its own public key and an identifier. Both
sides derive the same pre-shared key by ECDH and HKDF. Issuance does not
transmit the derived key.

Issuance is TLS-only and reachable before a credential exists: requiring a
pre-shared key to fetch the pre-shared key is circular. This platform API
bootstrap runs outside the protected tunnel.

THE DERIVATION MUST MATCH THE ISSUER BYTE FOR BYTE. A mismatch is silent — no
tunnel opens and every request is refused, far from the cause — so
`tests/test_platform_token.py` pins it against a vector the issuer produced.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Final, NamedTuple

import aiohttp
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import ValidationError

from dualeai.models.platform_token import PlatformTokenIssueRequest, PlatformTokenIssueResponse
from dualeai.models.problem_details import ProblemDetails

API_TOKEN_PREFIX: Final[str] = "dualeai_"
"""The prefix every platform credential carries.

Pinned here rather than imported: this is a published package and cannot import
a platform library. The derived key carries it because both sides build the same
string, and the golden vector is what keeps them equal.
"""

_HKDF_INFO: Final[bytes] = b"duale-platform-token/1"
"""Domain separation, versioned in the label. Matches the issuer exactly."""

_ISSUANCE_PATH: Final[str] = "/profile/platform-token"
"""Where the token service issues, behind the Gateway prefix the other services use."""


class PlatformToken(NamedTuple):
    """What a tunnel needs, and when it stops working."""

    psk_id: bytes
    psk: bytes
    valid_until: datetime


class PlatformTokenIssueError(Exception):
    """An issuer refusal with structured context kept out of the error string."""

    def __init__(self, status: int, problem_details: ProblemDetails | None) -> None:
        super().__init__(f"Platform token issuance refused with HTTP {status}")
        self.status = status
        self.problem_details = problem_details


def _now() -> datetime:
    """The current instant, as one name the rotation tests can hold still.

    Every deadline in this module is compared against this call. Freezing a
    clock is the only way to observe the advisory and mandatory bands without
    a test that sleeps, and a sleeping test would have to pick a duration that
    is either flaky or slow.
    """
    return datetime.now(timezone.utc)


def derive_psk(client_private: X25519PrivateKey, issuer_public_hex: str) -> str:
    """The shared secret, as the string the issuer recorded."""
    shared = client_private.exchange(X25519PublicKey.from_public_bytes(bytes.fromhex(issuer_public_hex)))
    material = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_HKDF_INFO).derive(shared)
    return API_TOKEN_PREFIX + material.hex()


async def issue(
    session: aiohttp.ClientSession,
    endpoint: str,
    api_token: str,
) -> PlatformToken:
    """Exchange ``api_token`` for a platform token.

    NO TENANT IS SENT. This client's only credential is an API token, which
    names exactly one tenant, so the issuer takes that one and there is nothing
    to choose.

    BOTH BODIES GO THROUGH THE GENERATED CONTRACT. Issuance runs before any
    credential exists, so the answer is the least trusted body this client
    handles: parsing it makes a malformed one a raised error here instead of a
    tunnel that never opens. `tests/test_platform_token_issuance.py` pins that.
    """
    private = X25519PrivateKey.generate()
    body = PlatformTokenIssueRequest(client_public_key=private.public_key().public_bytes_raw().hex())

    async with session.post(
        f"{endpoint.rstrip('/')}{_ISSUANCE_PATH}",
        json=body.model_dump(mode="json"),
        headers={"Authorization": f"Bearer {api_token}"},
    ) as response:
        if response.status != HTTPStatus.CREATED:
            try:
                problem = ProblemDetails.model_validate_json(await response.read())
            except ValidationError:
                problem = None
            # Proxy error pages and echoed request data must not become log text.
            # Typed callers retain the service's problem without parsing prose.
            raise PlatformTokenIssueError(response.status, problem)
        issued = PlatformTokenIssueResponse.model_validate(await response.json())

    return PlatformToken(
        psk_id=bytes.fromhex(issued.psk_id),
        psk=derive_psk(private, issued.issuer_public_key).encode("utf-8"),
        valid_until=issued.valid_until,
    )


class _Held(NamedTuple):
    """A token and the two instants that govern replacing it.

    One object, so :meth:`PlatformTokenProvider.get` reads the token and both
    deadlines in a single attribute access and can never see a token beside
    another token's deadlines.
    """

    token: PlatformToken
    refresh_at: datetime
    """When to start replacing it. Failing here is survivable."""
    renew_by: datetime
    """When the held token stops being usable cover. Failing here is the caller's."""


class PlatformTokenProvider:
    """Hold one platform token and replace it before it stops working.

    A PLATFORM TOKEN EXPIRES, so obtaining one at connect and keeping it is a
    defect that surfaces hours later as every request refused. This owns that
    lifecycle in one place: a caller asks for a token and gets a usable one,
    and rotation never reaches the API this SDK publishes.

    TWO BANDS, NOT ONE DEADLINE. This is ``RefreshableCredentials``' idea in
    botocore. In the advisory band the held token is still valid, so a failed
    reissue is survivable and the call proceeds on what we have; in the
    mandatory band we stop treating it as cover, so the failure belongs to the
    caller. With a single margin one issuer blip fails a request that a
    still-valid credential could have served.

    THE BANDS ARE FRACTIONS OF THE TOKEN'S OWN LIFETIME, where botocore uses
    fixed 15- and 10-minute timeouts. The issuer controls the lifetime: an AAL3
    token expires at its ceremony's freshness deadline, so it may have only
    seconds remaining when issued. A fixed 15-minute advisory margin would
    reissue a short token on every call, and a 10-minute mandatory margin could
    never be satisfied. Fractions scale to the validity the issuer returns.
    ``test_the_bands_scale_to_a_short_lived_token`` covers short-token rotation;
    SDK tests do not enforce the issuer's ceremony-freshness deadline.

    :meth:`invalidate` requests replacement after a possible credential refusal.
    The stamped expiry does not reveal early withdrawal or distinguish it from
    other causes of an outer transport refusal.
    """

    ADVISORY_FRACTION: Final[float] = 0.25
    """Start replacing with this share of the lifetime left, whatever that lifetime is."""

    MANDATORY_FRACTION: Final[float] = 0.10
    """Below this share left, a failed reissue is the caller's failure rather than a survivable one."""

    _JITTER_FRACTION: Final[float] = 0.05
    """Spread across a fleet, so agents issued together do not renew together.

    Taken from azure-core's refresh jitter, but drawn ONCE per token rather than
    on every check: a deadline that moves each time it is read makes the same
    question answerable two ways a millisecond apart.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        endpoint: str,
        api_token: str,
    ) -> None:
        self._session = session
        self._endpoint = endpoint
        self._api_token = api_token
        self._held: _Held | None = None
        self._issuing = asyncio.Lock()

    def _schedule(self, token: PlatformToken, *, now: datetime) -> _Held:
        """Place both deadlines inside this token's own lifetime."""
        lifetime = token.valid_until - now
        jitter = lifetime * self._JITTER_FRACTION * random.random()
        return _Held(
            token=token,
            # Jitter moves the advisory instant LATER, into the band between the
            # two fractions, so it can never cross `renew_by` and turn a
            # survivable reissue into a mandatory one.
            refresh_at=token.valid_until - lifetime * self.ADVISORY_FRACTION + jitter,
            renew_by=token.valid_until - lifetime * self.MANDATORY_FRACTION,
        )

    async def get(self) -> PlatformToken:
        """Return a token that will still resolve, issuing one if needed."""
        held = self._held
        now = _now()
        if held is not None and now < held.refresh_at:
            return held.token
        # ALREADY BEING REPLACED, AND WHAT WE HOLD IS STILL COVER. Waiting on the
        # lock here would make every concurrent request pay for one reissue, and
        # botocore's non-blocking advisory acquire exists for the same reason.
        if held is not None and now < held.renew_by and self._issuing.locked():
            return held.token

        async with self._issuing:
            held = self._held
            now = _now()
            if held is not None and now < held.refresh_at:
                return held.token
            # Decided BEFORE the attempt: whether a failure is survivable depends
            # on the cover we hold now, not on how long the attempt took.
            survivable = held is not None and now < held.renew_by
            try:
                issued = await issue(self._session, self._endpoint, self._api_token)
            except Exception:
                if not survivable:
                    raise
                # The held token still opens tunnels; the next call tries again.
                assert held is not None
                return held.token
            self._held = self._schedule(issued, now=now)
            return issued

    def invalidate(self) -> None:
        """Discard the held token so the next :meth:`get` issues a fresh one.

        This drops only the provider's reusable reference; it does not establish
        whether the issuer still holds the record. The transport owns keyed
        clients separately and retains them while admitted responses use them.
        ``test_invalidate_forces_the_next_call_to_reissue`` covers replacement;
        ``test_retired_tunnels_keep_admitted_streams_until_the_last_response_finishes``
        covers the transport's retention boundary.
        """
        self._held = None
