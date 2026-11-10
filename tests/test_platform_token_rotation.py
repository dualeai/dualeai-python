"""A platform token expires, and no caller of this SDK should ever learn that.

`PlatformTokenProvider` owns the credential's lifetime. These tests drive it
through the states a long-running agent actually reaches: a first issuance, a
token still good, a token in each of the two replacement bands, an issuer that
is briefly down, a token withdrawn early, and a burst of concurrent callers.

THE REPLACEMENT FRACTIONS ARE THE SUBJECT, NOT THE ISSUER'S LIFETIMES. The
issuer is stubbed here, so the two lifetimes below are inputs chosen for what
each test needs and not a claim about what the issuer hands out. The issuer's
own suite pins the numbers it issues; nothing here would notice them change,
and nothing here should — a fraction is correct for any lifetime.

THE CLOCK IS FROZEN, not slept through. The bands are fractions of the token's
own lifetime, so observing them by waiting would mean either a flaky test or a
slow one. `_now` is the single instant every deadline is compared against, so
moving it is the whole of time control here.

EVERY INSTANT BELOW IS A LITERAL OFFSET FROM `_EPOCH`, with the arithmetic that
produced it in a comment beside it. Nothing here reads `ADVISORY_FRACTION`,
`MANDATORY_FRACTION` or the jitter fraction. An oracle computed from those
constants moves whenever they do, so quadrupling the replacement rate leaves
every test green.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Final

import pytest

from dualeai import _platform_token
from dualeai._platform_token import API_TOKEN_PREFIX, PlatformToken, PlatformTokenProvider

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.unit

_EPOCH: Final[datetime] = datetime(2026, 1, 1, tzinfo=timezone.utc)
"""A fixed origin, so every instant below is a literal offset from one place."""

_LONG_LIFETIME: Final[timedelta] = timedelta(hours=10)
"""Room enough that every band is hours wide, for the tests about ordinary life."""

_SHORT_LIFETIME: Final[timedelta] = timedelta(minutes=4)
"""A lifetime a fixed-margin design would swallow whole.

Botocore's fixed 15-minute advisory margin would reissue a token this short on
every call, and its 10-minute mandatory margin could never be satisfied at all.
That is why the bands are fractions; `PlatformTokenProvider` records which
issuer lifetimes make it matter.
"""


class _Clock:
    """A settable instant, installed over `_platform_token._now`."""

    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


class _Issuer:
    """A counted stand-in for the issuer, with a switchable outage.

    Replaces `_platform_token.issue`, which is the first boundary this SDK does
    not own: everything below it is HTTP to another service.
    """

    def __init__(self, clock: _Clock, lifetime: timedelta = _LONG_LIFETIME) -> None:
        self._clock = clock
        self._lifetime = lifetime
        self.calls = 0
        self.down = False
        self.gate: asyncio.Event | None = None
        self.entered: asyncio.Event | None = None

    async def __call__(self, _session: object, _endpoint: str, _api_token: str) -> PlatformToken:
        self.calls += 1
        if self.entered is not None:
            self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.down:
            msg = "the issuer is unreachable"
            raise RuntimeError(msg)
        return PlatformToken(
            psk_id=bytes([self.calls]) * 64,
            psk=f"{API_TOKEN_PREFIX}{self.calls:064d}".encode(),
            valid_until=self._clock.at + self._lifetime,
        )


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    frozen = _Clock(_EPOCH)
    monkeypatch.setattr(_platform_token, "_now", frozen)
    return frozen


@pytest.fixture
def provider_for(
    clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., tuple[PlatformTokenProvider, _Issuer]]:
    def _build(lifetime: timedelta = _LONG_LIFETIME) -> tuple[PlatformTokenProvider, _Issuer]:
        issuer = _Issuer(clock, lifetime)
        monkeypatch.setattr(_platform_token, "issue", issuer)
        return (
            PlatformTokenProvider(
                session=None,  # ty: ignore[invalid-argument-type]
                endpoint="https://api.test",
                api_token=f"{API_TOKEN_PREFIX}x",
            ),
            issuer,
        )

    return _build


# THE TEN-HOUR TOKEN, as literal offsets from `_EPOCH`:
#   advisory instant   7h30m (25% of 10h left), plus up to 30m of jitter (5%)
#                      → 8h00m is the latest it can sit
#   mandatory instant  9h00m (10% of 10h left), fixed
#   expiry            10h00m
#
# THE FOUR-MINUTE TOKEN:
#   advisory instant   3m00s (25% of 4m left), plus up to 12s of jitter (5%)
#                      → 3m12s is the latest it can sit
#   mandatory instant  3m36s (10% of 4m left), fixed
#   expiry             4m00s

# ---------------------------------------------------------------------------
# The ordinary life of a token
# ---------------------------------------------------------------------------


async def test_the_first_call_obtains_a_token(provider_for) -> None:
    """Nothing is held until someone asks; construction makes no request."""
    provider, issuer = provider_for()
    assert issuer.calls == 0

    token = await provider.get()

    assert issuer.calls == 1
    assert token.psk.startswith(API_TOKEN_PREFIX.encode())


async def test_a_live_token_is_reused_rather_than_reissued(provider_for, clock: _Clock) -> None:
    """The common case must cost nothing: a held token well inside its life."""
    provider, issuer = provider_for()
    first = await provider.get()

    clock.at = _EPOCH + timedelta(hours=1)
    # THE SAME OBJECT, NOT AN EQUAL ONE. `PlatformToken` is a NamedTuple, so `==`
    # accepts a rebuilt copy, and `HTTPTransport._bind_tunnels` reuses its two
    # HPKE tunnels only on `await tokens.get() is self._held`. A provider that
    # returned an equal copy would rebuild both on every single request.
    assert await provider.get() is first
    assert issuer.calls == 1


async def test_crossing_the_advisory_instant_replaces_the_token(provider_for, clock: _Clock) -> None:
    """Rotation is proactive: no request has to fail first."""
    provider, issuer = provider_for()
    first = await provider.get()

    # 8h30m: past 8h00m, the latest the jitter could have placed the advisory
    # instant, so the crossing is certain.
    clock.at = _EPOCH + timedelta(hours=8, minutes=30)
    second = await provider.get()

    assert issuer.calls == 2
    assert second != first
    assert second.psk_id != first.psk_id


async def test_the_bands_scale_to_a_short_lived_token(provider_for, clock: _Clock) -> None:
    """A short-lived token must rotate inside its own lifetime, not be written off.

    This is the case a fixed-margin design gets wrong. With botocore's 15-minute
    advisory timeout every single call would reissue; with a 10-minute mandatory
    one no held token would ever be cover. The same fractions place both
    instants inside this lifetime, as they do inside any other.
    """
    provider, issuer = provider_for(_SHORT_LIFETIME)
    await provider.get()

    # 2m55s: before 3m00s, the earliest the advisory instant can sit, so the
    # token is still good however the jitter fell.
    clock.at = _EPOCH + timedelta(minutes=2, seconds=55)
    assert await provider.get() is not None
    assert issuer.calls == 1, "a short-lived token was replaced before its advisory instant"

    # 3m20s: past 3m12s, the latest it can sit.
    clock.at = _EPOCH + timedelta(minutes=3, seconds=20)
    await provider.get()
    assert issuer.calls == 2


async def test_invalidate_forces_the_next_call_to_reissue(provider_for, clock: _Clock) -> None:
    """Revocation deletes the record, and the stamped expiry says nothing about it."""
    provider, issuer = provider_for()
    first = await provider.get()

    provider.invalidate()
    clock.at = _EPOCH + timedelta(seconds=1)
    second = await provider.get()

    assert issuer.calls == 2
    assert second.psk_id != first.psk_id


# ---------------------------------------------------------------------------
# The two bands differ only in what a failure costs
# ---------------------------------------------------------------------------


async def test_an_issuer_outage_in_the_advisory_band_is_survived(provider_for, clock: _Clock) -> None:
    """The held token still opens tunnels, so the call proceeds on it.

    This is the whole reason there are two bands. A single margin would turn one
    issuer blip into a failed request that a valid credential could have served.
    """
    provider, issuer = provider_for()
    first = await provider.get()

    issuer.down = True
    # 8h30m: past the 8h00m advisory ceiling and before the 9h00m mandatory
    # instant, so the held token is still cover however the jitter fell.
    clock.at = _EPOCH + timedelta(hours=8, minutes=30)
    served = await provider.get()

    assert served == first, "the survivable path did not fall back on the held token"
    assert issuer.calls == 2, "no replacement was attempted at all"

    # And it keeps trying: the band is not a latch that gives up after one failure.
    issuer.down = False
    replaced = await provider.get()
    assert issuer.calls == 3
    assert replaced != first


async def test_an_issuer_outage_in_the_mandatory_band_reaches_the_caller(provider_for, clock: _Clock) -> None:
    """Past the mandatory instant there is no cover left, so the failure is real.

    Returning the held token here would hand the caller a credential about to
    stop resolving, and the refusal would surface inside an unrelated request.
    """
    provider, issuer = provider_for()
    await provider.get()

    issuer.down = True
    # 9h30m: past the 9h00m mandatory instant, so nothing held is cover.
    clock.at = _EPOCH + timedelta(hours=9, minutes=30)

    with pytest.raises(RuntimeError, match="unreachable"):
        await provider.get()
    assert issuer.calls == 2


async def test_a_first_issuance_failure_always_reaches_the_caller(provider_for) -> None:
    """With nothing held there is no cover, whatever the clock says."""
    provider, issuer = provider_for()
    issuer.down = True

    with pytest.raises(RuntimeError, match="unreachable"):
        await provider.get()
    assert issuer.calls == 1


@pytest.mark.parametrize("finished_at", [89, 90, 100, 101])
async def test_renewal_failure_uses_the_cover_deadline_when_it_finishes(
    provider_for, clock: _Clock, finished_at: int
) -> None:
    """A 100-second token remains fallback only before its 90-second deadline."""
    provider, issuer = provider_for(timedelta(seconds=100))
    first = await provider.get()
    clock.at = _EPOCH + timedelta(seconds=82)
    issuer.down = True
    issuer.entered = asyncio.Event()
    issuer.gate = asyncio.Event()
    renewal = asyncio.create_task(provider.get())
    try:
        await asyncio.wait_for(issuer.entered.wait(), timeout=1)
        clock.at = _EPOCH + timedelta(seconds=finished_at)
        issuer.gate.set()
        if finished_at < 90:
            assert await renewal is first
        else:
            with pytest.raises(RuntimeError, match="the issuer is unreachable"):
                await renewal
        assert issuer.calls == 2
    finally:
        issuer.gate.set()
        await asyncio.gather(renewal, return_exceptions=True)


async def test_invalidated_cover_is_not_returned_after_pending_renewal_fails(provider_for, clock: _Clock) -> None:
    """Early withdrawal during issuance cannot restore the discarded credential."""
    provider, issuer = provider_for(timedelta(seconds=100))
    await provider.get()
    clock.at = _EPOCH + timedelta(seconds=82)
    issuer.down = True
    issuer.entered = asyncio.Event()
    issuer.gate = asyncio.Event()
    renewal = asyncio.create_task(provider.get())
    try:
        await asyncio.wait_for(issuer.entered.wait(), timeout=1)
        provider.invalidate()
        issuer.gate.set()
        with pytest.raises(RuntimeError, match="the issuer is unreachable"):
            await renewal
        issuer.down = False
        replacement = await provider.get()
        assert replacement.psk_id == bytes([3]) * 64
    finally:
        issuer.gate.set()
        await asyncio.gather(renewal, return_exceptions=True)


async def test_a_delayed_issuance_schedules_from_receipt_time(
    provider_for, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Receipt at 20s with expiry at 120s starts the advisory band at 95s."""
    monkeypatch.setattr(_platform_token.random, "random", lambda: 0)
    provider, issuer = provider_for(timedelta(seconds=100))
    issuer.entered = asyncio.Event()
    issuer.gate = asyncio.Event()
    issuance = asyncio.create_task(provider.get())
    try:
        await asyncio.wait_for(issuer.entered.wait(), timeout=1)
        clock.at = _EPOCH + timedelta(seconds=20)
        issuer.gate.set()
        first = await issuance
        assert first.valid_until == _EPOCH + timedelta(seconds=120)

        clock.at = _EPOCH + timedelta(seconds=94)
        assert await provider.get() is first
        assert issuer.calls == 1
        clock.at = _EPOCH + timedelta(seconds=95)
        assert (await provider.get()).psk_id == bytes([2]) * 64
    finally:
        issuer.gate.set()
        await asyncio.gather(issuance, return_exceptions=True)


# ---------------------------------------------------------------------------
# Concurrency: a burst costs one round trip
# ---------------------------------------------------------------------------


async def test_concurrent_first_callers_share_one_issuance(provider_for) -> None:
    """Twenty coroutines starting together must not mint twenty records.

    Every one but the last would be unused in the issuer's store, and each is a
    row and a round trip.
    """
    provider, issuer = provider_for()
    issuer.gate = asyncio.Event()

    waiting = [asyncio.create_task(provider.get()) for _ in range(20)]
    await asyncio.sleep(0)
    issuer.gate.set()
    tokens = await asyncio.gather(*waiting)

    assert issuer.calls == 1
    assert len({token.psk_id for token in tokens}) == 1


async def test_a_caller_arriving_during_an_advisory_renewal_is_not_delayed(
    provider_for,
    clock: _Clock,
) -> None:
    """A renewal in flight must not make every concurrent request wait for it.

    The arriving caller still holds cover, so it takes the held token and goes.
    Blocking instead would convert one slow issuance into a stall across the
    whole process — botocore's non-blocking advisory acquire exists for this.
    """
    provider, issuer = provider_for()
    first = await provider.get()

    issuer.gate = asyncio.Event()
    # 8h30m: inside the advisory band, so the arriving caller still holds cover.
    clock.at = _EPOCH + timedelta(hours=8, minutes=30)
    renewal = asyncio.create_task(provider.get())
    await asyncio.sleep(0)

    # The renewal is blocked inside the issuer; this caller must not join it.
    assert await asyncio.wait_for(provider.get(), timeout=1) is first

    issuer.gate.set()
    assert await renewal != first
    assert issuer.calls == 2


# ---------------------------------------------------------------------------
# Jitter: a fleet issued together does not renew together
# ---------------------------------------------------------------------------


async def test_two_providers_over_one_lifetime_reach_two_refresh_instants(
    provider_for, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Jitter separates actual issuer requests from providers issued together."""
    draws = iter((0.0, 0.8, 0.0, 0.0))
    monkeypatch.setattr(_platform_token.random, "random", lambda: next(draws))
    first, _ = provider_for()
    second, issuer = provider_for()
    # Both providers use the same patched issuer; its count observes the fleet.
    first_token = await first.get()
    second_token = await second.get()
    assert first_token.valid_until == second_token.valid_until
    assert issuer.calls == 2

    # Ten hours gives a 7h30m advisory time and up to 30m of jitter.
    # The two draws place renewal at 7h30m and 7h54m respectively.
    clock.at = _EPOCH + timedelta(hours=7, minutes=40)
    renewed_first = await first.get()
    assert renewed_first != first_token
    assert await second.get() is second_token
    assert issuer.calls == 3

    clock.at = _EPOCH + timedelta(hours=8)
    assert await first.get() is renewed_first
    assert await second.get() != second_token
    assert issuer.calls == 4
