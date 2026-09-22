"""Typed HTTP/SSE client used by the public SDK facade.

Despite the compatibility name ``CloudEventsClient``, this class sends HTTP
request bodies and consumes Server-Sent Events. The transport owns reconnects
and ``Last-Event-ID`` handling. An injected transport provides the network
boundary used by tests.

Client dispatch and error translation are covered by
``tests/test_feature_ask.py`` and ``tests/test_libraries_client.py``.
"""

import types
from collections.abc import AsyncIterator, Callable
from typing import NoReturn

import structlog
from typing_extensions import Self

from dualeai.config import DualeAIConfig
from dualeai.events.http_transport import (
    HTTPTransport,
    HTTPTransportAuthError,
    HTTPTransportError,
    HTTPTransportResponseError,
)
from dualeai.events.transport import BridgeTaskRequest, HTTPTransportProtocol
from dualeai.exceptions import BusinessError, DualeAIAuthError, DualeAIConnectionError, DualeAIError
from dualeai.models.bridge import (
    AgentDeregistrationMessage,
    AgentHeartbeatMessage,
    AgentHeartbeatResponse,
    AgentRegistrationMessage,
    BridgeContentDeltaResponse,
    BridgeContentResetResponse,
    BridgeSSEEvent,
    BridgeTaskCompletedResponse,
    BridgeTaskErrorResponse,
    BridgeTaskStoppedResponse,
    BridgeToolResultsRequest,
    BridgeToolUseResponse,
)
from dualeai.models.task_stop import TaskStopAccepted, TaskStopRequest

DeltaCallback = Callable[[BridgeContentDeltaResponse], None]
ResetCallback = Callable[[BridgeContentResetResponse], None]
ToolUseCallback = Callable[[BridgeToolUseResponse, str | None], None]

logger = structlog.get_logger(__name__)


def _lift_problem_details(sdk_error: DualeAIError, cause: HTTPTransportError) -> DualeAIError:
    """Carry the RFC 9457 ProblemDetails from a transport error onto the SDK error.

    The bridge returns a typed ProblemDetails body on lifecycle and tool-result
    failures; without this the wrapped SDK error would expose only an HTTP status
    line and drop the ``error_code`` and retry hints.
    """
    sdk_error.problem_details = cause.problem_details
    return sdk_error


def _raise_translated(prefix: str, error: HTTPTransportError) -> NoReturn:
    """Translate a transport error into the matching SDK error and raise it.

    Authentication failures become ``DualeAIAuthError``; other client request
    rejections become ``BusinessError``; transport and server failures become
    ``DualeAIConnectionError``. Each carries the platform ProblemDetails when
    one was returned.
    """
    if isinstance(error, HTTPTransportAuthError):
        raise _lift_problem_details(DualeAIAuthError(f"{prefix}: {error}"), error) from error
    if isinstance(error, HTTPTransportResponseError):
        raise _lift_problem_details(BusinessError(f"{prefix}: {error}"), error) from error
    raise _lift_problem_details(DualeAIConnectionError(f"{prefix}: {error}"), error) from error


def _dispatch_stream_event(
    data: object,
    event_id: str | None,
    delta_callback: DeltaCallback | None,
    reset_callback: ResetCallback | None,
    tool_use_callback: ToolUseCallback | None,
) -> BridgeTaskCompletedResponse | BridgeTaskErrorResponse | BridgeTaskStoppedResponse | None:
    """Route one SSE event payload to its callback; return the terminal event or None.

    Content-delta, content-reset, and tool-use events invoke their callback
    (when provided) and return None so the caller keeps iterating. A terminal
    event is returned so the caller stops and yields it.
    """
    if isinstance(data, BridgeContentDeltaResponse):
        if delta_callback is not None:
            delta_callback(data)
        return None
    if isinstance(data, BridgeContentResetResponse):
        if reset_callback is not None:
            reset_callback(data)
        return None
    if isinstance(data, BridgeToolUseResponse):
        if tool_use_callback is not None:
            tool_use_callback(data, event_id)
        return None
    if isinstance(data, (BridgeTaskCompletedResponse, BridgeTaskErrorResponse, BridgeTaskStoppedResponse)):
        return data
    return None


class CloudEventsClient:
    """Lower-level client for Task streams, lifecycle calls, and Libraries.

    Applications normally use ``DualeAISDK`` rather than constructing this
    compatibility-named class directly. The client sends the configured API
    token; it does not itself establish authorization or tenant isolation.
    """

    def __init__(
        self,
        config: DualeAIConfig,
        transport: HTTPTransportProtocol | None = None,
    ):
        """Initialize CloudEventsClient with HTTP transport.

        Args:
            config: SDK configuration with token and endpoint
            transport: Optional pre-configured transport for dependency injection.
                       If None, HTTPTransport will be created during connect().
        """
        self.config = config
        self._transport: HTTPTransportProtocol | None = transport
        self._connected = False

    async def connect(self) -> None:
        """Connect to HTTP bridge.

        Raises:
            DualeAIAuthError: If authentication fails (invalid/expired API key)
            DualeAIConnectionError: If connection fails
        """
        try:
            # If transport was injected (e.g., MockHTTPTransport for testing), use it
            if self._transport is not None:
                if not self._transport.is_connected:
                    await self._transport.connect()
                self._connected = True

                logger.debug("HTTP transport connected (injected)")
                return

            # Create HTTPTransport for production
            logger.debug(
                "Starting HTTP connection",
                endpoint=self.config.endpoint,
            )

            # config.token is always str — field_validator raises if None/missing
            token = self.config.token
            assert token is not None, "config.token must be set (validated by DualeAIConfig)"
            http_transport = HTTPTransport(
                endpoint=self.config.endpoint,
                token=token,
                tenant_id=self.config.tenant_id,
            )
            await http_transport.connect()

            self._transport = http_transport
            self._connected = True

            logger.info(
                "Connected to HTTP bridge",
                endpoint=self.config.endpoint,
            )

        except HTTPTransportAuthError as e:
            logger.error(
                "Authentication failed",
                endpoint=self.config.endpoint,
                error=str(e),
            )
            raise DualeAIAuthError(f"Authentication failed: {e}") from e
        except HTTPTransportError as e:
            logger.error(
                "HTTP connection failed",
                endpoint=self.config.endpoint,
                error=str(e),
            )
            raise DualeAIConnectionError(f"Failed to connect to HTTP bridge: {e}") from e
        except Exception as e:
            logger.error(
                "Unexpected error connecting to HTTP bridge",
                error=str(e),
                exc_info=True,
            )
            raise DualeAIConnectionError(f"Failed to connect: {e}") from e

    def _ensure_connected(self) -> HTTPTransportProtocol:
        """Validate the transport is connected; return it for use.

        Raises:
            DualeAIConnectionError: not yet connected.
        """
        if not self._connected or self._transport is None:
            raise DualeAIConnectionError("Not connected. Call connect() first.")
        return self._transport

    async def disconnect(self) -> None:
        """Disconnect from HTTP bridge and cleanup resources."""
        if self._transport and self._transport.is_connected:
            try:
                await self._transport.disconnect()
                logger.info("Disconnected from HTTP bridge")
            except Exception as e:  # noqa: BLE001  # Suppress cleanup errors
                logger.warning(
                    "Error during HTTP disconnect",
                    error=str(e),
                )
            finally:
                self._connected = False
                self._transport = None

    async def run_task(
        self,
        *,
        task_id: str,
        request: BridgeTaskRequest,
        delta_callback: DeltaCallback | None = None,
        reset_callback: ResetCallback | None = None,
        tool_use_callback: ToolUseCallback | None = None,
    ) -> BridgeTaskCompletedResponse | BridgeTaskErrorResponse | BridgeTaskStoppedResponse:
        """Submit one typed task request and consume its task-keyed stream.

        The SDK owns ``task_id`` and constructs ``request`` before this
        boundary. Create and continuation requests therefore share the same
        transport and terminal-event path.

        Args:
            task_id: Client-owned ID in the HTTP task path. The request decides
                whether it represents a root or continuation child.
            request: Validated create or continuation request body.
            delta_callback: Sync callback invoked on each
                ``BridgeContentDeltaResponse``. Must be fast — runs on
                the SSE iteration coroutine.
            reset_callback: Sync callback invoked when previously emitted
                content must be discarded before rendering replacement output.
            tool_use_callback: Sync callback invoked on each
                ``BridgeToolUseResponse`` with its SSE event id. Same
                fast-callback rule.

        Returns:
            The terminal event — ``BridgeTaskCompletedResponse`` on
            success, ``BridgeTaskErrorResponse`` on
            failure/timeout/cancellation.

        Raises:
            DualeAIAuthError: If authentication fails (401/403).
            DualeAIConnectionError: If not connected, the stream ends
                without a terminal event, or other transport errors.
        """
        transport = self._ensure_connected()
        logger.debug(
            "Running task",
            task_id=task_id,
            request_type=request.type,
        )
        operation = "Task continuation" if request.type == "continue" else "Task run"
        return await self._consume_terminal_stream(
            task_id=task_id,
            stream=transport.run_task(task_id, request),
            operation=operation,
            delta_callback=delta_callback,
            reset_callback=reset_callback,
            tool_use_callback=tool_use_callback,
        )

    async def _consume_terminal_stream(
        self,
        *,
        task_id: str,
        stream: AsyncIterator[BridgeSSEEvent],
        operation: str,
        delta_callback: DeltaCallback | None,
        reset_callback: ResetCallback | None,
        tool_use_callback: ToolUseCallback | None,
    ) -> BridgeTaskCompletedResponse | BridgeTaskErrorResponse | BridgeTaskStoppedResponse:
        """Consume one bridge SSE stream through the shared terminal contract."""
        try:
            async for event in stream:
                terminal = _dispatch_stream_event(
                    event.data,
                    event.id,
                    delta_callback,
                    reset_callback,
                    tool_use_callback,
                )
                if terminal is not None:
                    logger.info("Task terminated", task_id=task_id, outcome=type(terminal).__name__)
                    return terminal
        except HTTPTransportError as error:
            _raise_translated(f"{operation} failed", error)
        raise DualeAIConnectionError(f"{operation} stream for task {task_id} ended without a terminal event")

    async def stop_task(self, task_id: str, request: TaskStopRequest) -> TaskStopAccepted:
        """Ask the platform to stop a running task."""
        if self._transport is None:
            raise DualeAIConnectionError("Transport is not connected")
        try:
            return await self._transport.stop_task(task_id, request)
        except HTTPTransportError as error:
            _raise_translated("Task stop failed", error)

    async def submit_tool_results(
        self,
        *,
        task_id: str,
        request: BridgeToolResultsRequest,
        last_event_id: str | None = None,
        delta_callback: DeltaCallback | None = None,
        reset_callback: ResetCallback | None = None,
        tool_use_callback: ToolUseCallback | None = None,
    ) -> BridgeTaskCompletedResponse | BridgeTaskErrorResponse | BridgeTaskStoppedResponse:
        """Submit tool results and consume the same public task stream to terminal.

        ``task_id`` is the task that emitted ``tool.use``. ``last_event_id`` is
        that event's exact composite SSE cursor, so replay resumes after the
        triggering event instead of executing it again in this stream.
        """
        transport = self._ensure_connected()
        return await self._consume_terminal_stream(
            task_id=task_id,
            stream=transport.submit_tool_results(
                task_id=task_id,
                request=request,
                last_event_id=last_event_id,
            ),
            operation="Tool results submission",
            delta_callback=delta_callback,
            reset_callback=reset_callback,
            tool_use_callback=tool_use_callback,
        )

    async def register_agent_manifest(self, request: AgentRegistrationMessage) -> None:
        """Register the SDK tool manifest through the bridge."""
        transport = self._ensure_connected()
        try:
            await transport.register_agent_manifest(request)
        except HTTPTransportError as e:
            _raise_translated("Agent registration failed", e)

    async def send_agent_heartbeat(self, request: AgentHeartbeatMessage) -> AgentHeartbeatResponse:
        """Send one SDK heartbeat through the bridge."""
        transport = self._ensure_connected()
        try:
            return await transport.send_agent_heartbeat(request)
        except HTTPTransportError as e:
            _raise_translated("Agent heartbeat failed", e)

    async def deregister_agent_process(self, request: AgentDeregistrationMessage) -> None:
        """Send one SDK process deregistration through the bridge."""
        transport = self._ensure_connected()
        try:
            await transport.deregister_agent_process(request)
        except HTTPTransportError as e:
            _raise_translated("Agent deregistration failed", e)

    @property
    def transport(self) -> HTTPTransportProtocol:
        """Get the connected transport.

        Raises:
            RuntimeError: If not connected.
        """
        if self._transport is None:
            raise RuntimeError("Not connected. Call connect() first.")
        return self._transport

    @property
    def is_connected(self) -> bool:
        """Check if client is connected to HTTP bridge."""
        return self._connected and self._transport is not None and self._transport.is_connected

    async def __aenter__(self) -> Self:
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: types.TracebackType | None,
    ) -> None:
        """Async context manager exit."""
        await self.disconnect()
