"""Hosted-agent liveness: registration, heartbeat, deregistration, server clock.

``LifecycleManager`` is the RFC-121 liveness collaborator extracted from ``DualeAISDK``
(the Temporal ``Worker`` role — execution/liveness that borrows a connection, never the
front door). It owns the disjoint lifecycle state island (process id, clock offset,
heartbeat task, registration bookkeeping) and depends on the SDK only through two
injected callables — an events-client accessor and a tool-manifest accessor — so it
never imports ``DualeAISDK`` (no object cycle).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib.metadata
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import structlog
from uuid_utils import uuid7

from dualeai._canonical import canonical_json_bytes
from dualeai._wire import dump_wire_model
from dualeai.constants import TimingDefaults
from dualeai.events.client import CloudEventsClient
from dualeai.exceptions import DualeAIAuthError
from dualeai.models.bridge import (
    AgentDeregistrationMessage,
    AgentHeartbeatMessage,
    AgentHeartbeatResponse,
    AgentRegistrationMessage,
    HeartbeatStatus,
    RegisteredTool,
)

logger = structlog.get_logger(__name__)


def _sdk_version() -> str:
    """Return the installed SDK package version, with a dev fallback."""
    try:
        return importlib.metadata.version("dualeai")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0+local"


def _new_uuid7() -> UUID:
    """Return a uuid-utils UUIDv7 value as a standard-library UUID."""
    return UUID(str(uuid7()))


@dataclass(frozen=True)
class _ManifestSnapshot:
    """One name-sorted manifest and the hash of its canonical wire form."""

    tools: tuple[RegisteredTool, ...]
    config_hash: str


class LifecycleManager:
    """Owns agent registration + heartbeat + the server-time clock (RFC-121).

    Borrows (never imports a sibling): ``ensure_events_client`` (a
    ``ConnectionManager``-style accessor on the SDK) and ``manifest`` (the tool
    registry read, ``DualeAISDK.registered_tools``). Exports the clock via
    :meth:`estimated_server_time`, which the tool-execution path reads for deadline math.
    """

    def __init__(
        self,
        *,
        ensure_events_client: Callable[[], Awaitable[CloudEventsClient]],
        manifest: Callable[[], Sequence[RegisteredTool]],
        agent_id: str | None,
        auto_start: bool,
    ) -> None:
        self.agent_id = agent_id
        self._auto_start = auto_start
        self._ensure_events_client = ensure_events_client
        self._manifest = manifest
        self._process_id = _new_uuid7()
        self._clock_offset = timedelta(0)
        self._last_registered_config_hash: str | None = None
        self._next_registration_refresh_at: datetime | None = None
        self._manifest_publication_id: UUID | None = None
        self._manifest_publication_config_hash: str | None = None
        self._lifecycle_registered = False
        self._lifecycle_jitter_random = random.Random()
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._startup_task: asyncio.Task[None] | None = None
        self._startup_lock = asyncio.Lock()
        self._startup_done = False
        # Auto-start if enabled (create_task is a no-op with no running loop yet).
        self._maybe_auto_start()

    def _maybe_auto_start(self) -> None:
        """Schedule auto-startup, surfacing a failure a bare auto_start would swallow."""
        if not self._auto_start:
            return
        # suppress: create_task raises RuntimeError when no loop is running yet.
        with contextlib.suppress(RuntimeError):
            self._startup_task = asyncio.create_task(self.start())
            self._startup_task.add_done_callback(self._log_auto_startup_failure)

    @staticmethod
    def _log_auto_startup_failure(task: asyncio.Task[None]) -> None:
        """Log an auto-startup failure so a bare auto_start (no serve()/start()) is not silent.

        Retrieving exception() also marks it retrieved, avoiding the asyncio
        "exception was never retrieved" warning; serve()/start() still re-raises
        it on await when the developer drives startup explicitly.
        """
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error("Auto-startup failed", error=str(error))

    async def start(self) -> None:
        """Manually start SDK agent registration and heartbeat.

        This is a no-op if SDK was already started via auto_start=True.
        Safe to call multiple times - will only start once.
        """
        async with self._startup_lock:
            if not self._startup_done:
                await self._start_lifecycle()
                self._startup_done = True

    async def serve(
        self,
        *,
        stop_event: asyncio.Event | None = None,
        on_stop: Callable[[], Awaitable[None]],
    ) -> None:
        """Run the hosted-agent lifecycle until stopped or lifecycle failure.

        ``on_stop`` is the SDK-global teardown (``DualeAISDK.cleanup``): the loop lives
        here, but resource ownership stays at the facade, so the manager never holds a
        back-reference to ``DualeAISDK``.
        """
        lifecycle_stop = stop_event or asyncio.Event()
        stop_task: asyncio.Task[None] | None = None

        try:
            await self.start()

            async def wait_for_stop() -> None:
                await lifecycle_stop.wait()

            stop_task = asyncio.create_task(wait_for_stop())
            heartbeat_task = self._heartbeat_task
            wait_tasks = {stop_task}
            if heartbeat_task is not None:
                wait_tasks.add(heartbeat_task)
            done, _pending = await asyncio.wait(wait_tasks, return_when=asyncio.FIRST_COMPLETED)
            if heartbeat_task is not None and heartbeat_task in done:
                await heartbeat_task
        finally:
            if stop_task is not None:
                stop_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await stop_task
            await on_stop()

    def _require_lifecycle_agent_id(self) -> str:
        """Return the pre-provisioned agent id required by lifecycle endpoints."""
        if self.agent_id is None:
            raise RuntimeError(
                "SDK lifecycle requires agent_id. Configure DualeAISDK(agent_id=...) or DUALEAI_AGENT_ID."
            )
        return self.agent_id

    def estimated_server_time(self, local_now: datetime | None = None) -> datetime:
        """Return local time adjusted by the latest heartbeat clock offset."""
        return (local_now or datetime.now(timezone.utc)) + self._clock_offset

    @property
    def heartbeat_task(self) -> asyncio.Task[None] | None:
        """The running heartbeat task, or None before serve()/start()."""
        return self._heartbeat_task

    def config_hash(self) -> str:
        """Hash the canonical registered-tool manifest.

        The SDK serializes its validated wire JSON with a frozen byte recipe.
        The hash identifies manifest revisions sent in registration and
        heartbeat messages; it is not recomputed as a server-side security
        check.
        """
        return self._manifest_snapshot().config_hash

    def _manifest_snapshot(self) -> _ManifestSnapshot:
        """Read, sort, serialize, and hash the current manifest exactly once."""
        tools = tuple(sorted(self._manifest(), key=lambda registered_tool: registered_tool.tool.name))
        canonical_tools = [dump_wire_model(tool) for tool in tools]
        config_hash = hashlib.sha256(canonical_json_bytes(canonical_tools)).hexdigest()
        return _ManifestSnapshot(tools=tools, config_hash=config_hash)

    def _registration_message(self, snapshot: _ManifestSnapshot | None = None) -> AgentRegistrationMessage:
        """Build a generated Bridge registration model for the current manifest."""
        snapshot = snapshot or self._manifest_snapshot()
        if self._manifest_publication_config_hash != snapshot.config_hash or self._manifest_publication_id is None:
            self._manifest_publication_id = _new_uuid7()
            self._manifest_publication_config_hash = snapshot.config_hash
        return AgentRegistrationMessage(
            agent_id=self._require_lifecycle_agent_id(),
            process_id=self._process_id,
            tools=list(snapshot.tools),
            config_hash=snapshot.config_hash,
            manifest_publication_id=self._manifest_publication_id,
            time=self.estimated_server_time(),
            sdk_version=_sdk_version(),
        )

    def _heartbeat_message(
        self,
        *,
        local_sent_at: datetime,
        snapshot: _ManifestSnapshot | None = None,
    ) -> AgentHeartbeatMessage:
        """Build a generated Bridge heartbeat model for this SDK process."""
        snapshot = snapshot or self._manifest_snapshot()
        return AgentHeartbeatMessage(
            agent_id=self._require_lifecycle_agent_id(),
            process_id=self._process_id,
            status=HeartbeatStatus.healthy,
            time=self.estimated_server_time(local_sent_at),
            config_hash=snapshot.config_hash,
            heartbeat_publication_id=_new_uuid7(),
        )

    def _deregistration_message(self, *, reason: str) -> AgentDeregistrationMessage:
        """Build a generated Bridge deregistration model for this SDK process."""
        return AgentDeregistrationMessage(
            agent_id=self._require_lifecycle_agent_id(),
            process_id=self._process_id,
            deregistration_publication_id=_new_uuid7(),
            reason=reason,
            time=self.estimated_server_time(),
        )

    async def _send_registration(self, snapshot: _ManifestSnapshot | None = None) -> _ManifestSnapshot:
        """Publish the current tool manifest through the bridge."""
        snapshot = snapshot or self._manifest_snapshot()
        client = await self._ensure_events_client()
        message = self._registration_message(snapshot)
        self._lifecycle_registered = True
        await client.register_agent_manifest(message)
        self._last_registered_config_hash = message.config_hash
        self._schedule_next_registration_refresh()
        return snapshot

    def _jittered_interval_seconds(self, *, base_seconds: float, jitter_seconds: float) -> float:
        """Return a bounded jittered interval; zero base remains zero for tests."""
        if base_seconds <= 0:
            return 0.0
        jitter = self._lifecycle_jitter_random.uniform(-jitter_seconds, jitter_seconds)
        return max(0.0, base_seconds + jitter)

    def _heartbeat_sleep_seconds(self) -> float:
        """Return the next heartbeat sleep interval."""
        return self._jittered_interval_seconds(
            base_seconds=TimingDefaults.HEARTBEAT_INTERVAL_SECONDS,
            jitter_seconds=TimingDefaults.HEARTBEAT_JITTER_SECONDS,
        )

    def _schedule_next_registration_refresh(self, *, now: datetime | None = None) -> None:
        """Schedule the next full manifest anti-entropy refresh."""
        base_time = now or datetime.now(timezone.utc)
        interval_seconds = self._jittered_interval_seconds(
            base_seconds=TimingDefaults.REGISTRATION_REFRESH_INTERVAL_SECONDS,
            jitter_seconds=TimingDefaults.REGISTRATION_REFRESH_JITTER_SECONDS,
        )
        self._next_registration_refresh_at = base_time + timedelta(seconds=interval_seconds)

    async def _ensure_manifest_registered(self, snapshot: _ManifestSnapshot) -> None:
        """Refresh the full manifest before heartbeats can reference it."""
        refresh_due = (
            self._next_registration_refresh_at is None
            or datetime.now(timezone.utc) >= self._next_registration_refresh_at
        )
        if self._last_registered_config_hash != snapshot.config_hash or refresh_due:
            await self._send_registration(snapshot)

    def _update_clock_offset(
        self,
        *,
        local_sent_at: datetime,
        local_received_at: datetime,
        response: AgentHeartbeatResponse,
    ) -> None:
        """Use bridge timestamps to estimate server-time offset."""
        round_trip = local_received_at - local_sent_at
        if round_trip > timedelta(seconds=5):
            # A discarded sample leaves the offset unchanged; persistent high
            # round-trips freeze it (default 0) and skew tool-deadline math, so
            # surface the discard rather than dropping it silently.
            logger.warning(
                "Discarding heartbeat clock-offset sample; round-trip too high",
                round_trip_seconds=round_trip.total_seconds(),
            )
            return

        server_midpoint = response.server_received_at + (response.server_sent_at - response.server_received_at) / 2
        local_midpoint = local_sent_at + round_trip / 2
        self._clock_offset = server_midpoint - local_midpoint
        # Diagnostic only: the platform never trusts SDK-sent time (it stamps its own
        # received_at). The offset improves the advisory lifecycle `time` and local
        # deadline estimation; surface it for troubleshooting clock skew.
        logger.debug(
            "Updated heartbeat clock-offset estimate",
            clock_offset_seconds=round(self._clock_offset.total_seconds(), 3),
        )

    async def _send_heartbeat_once(self, snapshot: _ManifestSnapshot | None = None) -> AgentHeartbeatResponse:
        """Publish one heartbeat and update the server clock offset estimate."""
        snapshot = snapshot or self._manifest_snapshot()
        await self._ensure_manifest_registered(snapshot)
        client = await self._ensure_events_client()
        local_sent_at = datetime.now(timezone.utc)
        response = await client.send_agent_heartbeat(
            self._heartbeat_message(local_sent_at=local_sent_at, snapshot=snapshot)
        )
        local_received_at = datetime.now(timezone.utc)
        self._update_clock_offset(
            local_sent_at=local_sent_at,
            local_received_at=local_received_at,
            response=response,
        )
        return response

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats until cleanup cancels the task.

        A single auth failure is tolerated and retried on the next beat (a
        token-rotation blip or a brief Profile authz-cache deny should not tear
        down serve()); a second consecutive auth failure is a hard revocation and
        stops the loop. Other failures are treated as transient and tolerated up
        to TimingDefaults.MAX_CONSECUTIVE_HEARTBEAT_FAILURES consecutive misses, so
        a single network blip does not tear down serve() — the platform holds the
        agent online across two missed beats (RFC-121). A successful beat resets
        both counters.
        """
        consecutive_failures = 0
        consecutive_auth_failures = 0
        while True:
            await asyncio.sleep(self._heartbeat_sleep_seconds())
            try:
                await self._send_heartbeat_once()
                consecutive_failures = 0
                consecutive_auth_failures = 0
            except asyncio.CancelledError:
                raise
            except DualeAIAuthError:
                # An intervening non-auth failure breaks the auth streak (and vice
                # versa), so each branch resets the other counter — "consecutive"
                # means back-to-back of the SAME kind.
                consecutive_failures = 0
                consecutive_auth_failures += 1
                if consecutive_auth_failures >= TimingDefaults.MAX_CONSECUTIVE_HEARTBEAT_AUTH_FAILURES:
                    logger.exception("Agent lifecycle heartbeat rejected as unauthorized; stopping serve")
                    raise
                logger.warning(
                    "Agent lifecycle heartbeat rejected as unauthorized; retrying once before stopping",
                    consecutive_auth_failures=consecutive_auth_failures,
                )
            except Exception:
                consecutive_auth_failures = 0
                consecutive_failures += 1
                if consecutive_failures >= TimingDefaults.MAX_CONSECUTIVE_HEARTBEAT_FAILURES:
                    logger.exception(
                        "Agent lifecycle heartbeat failed repeatedly; stopping serve",
                        consecutive_failures=consecutive_failures,
                    )
                    raise
                logger.warning(
                    "Agent lifecycle heartbeat failed; retrying next interval",
                    consecutive_failures=consecutive_failures,
                )

    async def _start_lifecycle(self) -> None:
        """Register the manifest and start heartbeat for agent-bound SDKs."""
        if self.agent_id is None:
            if self._manifest():
                raise RuntimeError("SDK tools require agent_id before serve() or start() can publish lifecycle")
            return
        snapshot = await self._send_registration()
        try:
            await self._send_heartbeat_once(snapshot)
        except Exception:
            await self._deregister_lifecycle(reason="startup_failed")
            raise
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _deregister_lifecycle(self, *, reason: str = "shutdown") -> None:
        """Best-effort deregistration for the current SDK process."""
        if not self._lifecycle_registered or self.agent_id is None:
            return
        with contextlib.suppress(Exception):
            client = await self._ensure_events_client()
            await client.deregister_agent_process(self._deregistration_message(reason=reason))
        self._lifecycle_registered = False

    @staticmethod
    async def _cancel_task(task: asyncio.Task[None] | None) -> None:
        """Cancel a task and await its termination, suppressing the cancellation."""
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def aclose(self) -> None:
        """Cancel the startup + heartbeat tasks and deregister (called by DualeAISDK.cleanup)."""
        await self._cancel_task(self._startup_task)
        self._startup_task = None
        await self._cancel_task(self._heartbeat_task)
        self._heartbeat_task = None
        await self._deregister_lifecycle(reason="shutdown")
        self._startup_done = False
