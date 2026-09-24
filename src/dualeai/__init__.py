"""Public imports and the convenience factory for the Duale AI Python SDK."""

# Type hints
from typing import TypedDict

from pydantic import BaseModel, ConfigDict
from typing_extensions import Unpack

# UUID utilities
from uuid_utils import uuid7

# Attachments
from dualeai.attachments import PreparedAttachment, prepare_attachments

# Cache backends
from dualeai.cache import CacheBackend, CacheConfig, RedisCacheBackend, SQLiteCacheBackend, create_cache_backend

# Configuration
from dualeai.config import DualeAIConfig
from dualeai.constants import TimingDefaults
from dualeai.decorators import (
    activity,
    tool,
)

# Exceptions
from dualeai.exceptions import (
    BusinessError,
    ConfigurationError,
    DualeAIAuthError,
    DualeAIConnectionError,
    DualeAIError,
    LibraryUploadError,
    MessagingError,
    TaskStoppedError,
    ValidationError,
)

# Libraries
from dualeai.libraries import LibrariesClient

# Logging
from dualeai.logging_config import configure_logging, get_logger

# Task-stream wire models
from dualeai.models.bridge import (
    BridgeContentDeltaResponse,
    BridgeContentResetResponse,
    BridgeSSEEvent,
    BridgeTaskCompletedResponse,
    BridgeTaskContinueRequest,
    BridgeTaskCreateRequest,
    BridgeTaskErrorResponse,
    BridgeTaskStoppedResponse,
    BridgeToolResultError,
    BridgeToolResultsRequest,
    BridgeToolResultSuccess,
    BridgeToolUseResponse,
)
from dualeai.models.library import (
    LibraryCreateRequest,
    LibraryDeleteRequest,
    LibraryDocumentCreateResponse,
    LibraryDocumentDeleteRequest,
    LibraryDocumentGetRequest,
    LibraryDocumentListRequest,
    LibraryDocumentPage,
    LibraryGetRequest,
    LibraryListResponse,
    LibraryPatchRequest,
    LibraryResponseDocumentStatus,
    LibraryUpdateRequest,
    LibraryWithRevision,
    PublicIndexedDocument,
)
from dualeai.models.response_format import ResponseFormat
from dualeai.models.routing_policy import RoutingPolicy
from dualeai.models.skill_enum import SkillEnum
from dualeai.models.tool import Tool

# Orchestration
from dualeai.orchestrator import ask, continue_conversation

# Core SDK
from dualeai.sdk import DualeAISDK

# Tool runtime context (call identity + cooperative deadline). The call id is
# provider-issued and not unique on its own; see ToolContext.tool_call_id.
from dualeai.tool_context import ToolContext, current_tool_context

# Version
from dualeai.version import __version__


# Type definitions for better type safety
class SDKConfigKwargs(TypedDict, total=False):
    """Keyword arguments accepted by :func:`create_sdk`.

    Configuration via environment variables:
    - DUALEAI_TOKEN: API token for protected requests; starts with dualeai_ [required]
    - DUALEAI_ENDPOINT: HTTPS Gateway base URL for hpke-http/3 APIs [optional]
    - DUALEAI_TENANT_ID: Tenant path segment [required for Library operations]
    - DUALEAI_AGENT_ID: Provisioned identity [required for hosted Tools unless
      supplied programmatically; used by attachment uploads unless overridden per call]
    - DUALEAI_REDIS_URL: Redis server URL for caching [optional]
    """

    token: str  # API token for protected requests; starts with dualeai_
    endpoint: str  # HTTPS Gateway base URL for protected APIs
    tenant_id: str
    agent_id: str
    redis_url: str
    sqlite_path: str  # Will be converted to Path by Pydantic
    debug: bool | str  # Pydantic converts string to bool
    max_jobs: int | str  # SDK constructor parameter, Pydantic converts string to int
    job_timeout: int | str  # SDK constructor parameter, Pydantic converts string to int
    auto_start: bool | str  # SDK constructor parameter, Pydantic converts string to bool


class _SDKConstructorConfig(BaseModel):
    """Pydantic boundary for SDK constructor-only keyword arguments."""

    model_config = ConfigDict(extra="forbid")

    max_jobs: int = TimingDefaults.DEFAULT_MAX_JOBS
    job_timeout: int = TimingDefaults.DEFAULT_TASK_TIMEOUT_SECONDS
    auto_start: bool = False


def create_sdk(**kwargs: Unpack[SDKConfigKwargs]) -> DualeAISDK:
    """Create an SDK from flat configuration and constructor overrides.

    Unlike direct ``DualeAISDK`` construction, this factory defaults
    ``auto_start`` to ``False``. Its typed surface exposes the three constructor
    controls in ``SDKConfigKwargs`` but not dependency injection, nested
    observability configuration, Tool concurrency, graceful shutdown, or
    backpressure; use ``DualeAISDK`` with ``DualeAIConfig`` for those cases.
    Runtime keys not declared by ``DualeAIConfig`` are currently ignored by its
    Pydantic ``extra="ignore"`` policy, so use type checking to catch misspelled
    keyword names.

    Construction reconfigures process-global logging. See
    :func:`configure_logging` before using the factory in a host that owns its
    root logging setup.

    Constructor-argument coercion is covered by
    ``tests/test_agent_lifecycle.py::test_create_sdk_coerces_constructor_arguments_with_pydantic``.
    No focused automated test currently covers ignored unknown factory keys.

    Args:
        **kwargs: Keys declared by ``SDKConfigKwargs``. ``max_jobs``,
            ``job_timeout``, and ``auto_start`` configure the SDK constructor;
            remaining keys configure ``DualeAIConfig``.

    Returns:
        A configured SDK instance. Network and cache resources remain lazy.

    Raises:
        pydantic.ValidationError: If a supplied configuration value is invalid.
    """
    config_kwargs = dict(kwargs)

    constructor_config = _SDKConstructorConfig.model_validate(
        {
            "max_jobs": config_kwargs.pop("max_jobs", TimingDefaults.DEFAULT_MAX_JOBS),
            "job_timeout": config_kwargs.pop("job_timeout", TimingDefaults.DEFAULT_TASK_TIMEOUT_SECONDS),
            "auto_start": config_kwargs.pop("auto_start", False),
        }
    )
    agent_id = config_kwargs.pop("agent_id", None)

    config = DualeAIConfig.model_validate(config_kwargs)
    return DualeAISDK(
        config=config,
        agent_id=agent_id if agent_id is None else str(agent_id),
        max_jobs=constructor_config.max_jobs,
        job_timeout=constructor_config.job_timeout,
        auto_start=constructor_config.auto_start,
    )


__all__ = [
    "BridgeContentDeltaResponse",
    "BridgeContentResetResponse",
    "BridgeSSEEvent",
    "BridgeTaskCompletedResponse",
    "BridgeTaskContinueRequest",
    "BridgeTaskCreateRequest",
    "BridgeTaskErrorResponse",
    "BridgeTaskStoppedResponse",
    "BridgeToolResultError",
    "BridgeToolResultSuccess",
    "BridgeToolResultsRequest",
    "BridgeToolUseResponse",
    "BusinessError",
    "CacheBackend",
    "CacheConfig",
    "ConfigurationError",
    "DualeAIAuthError",
    "DualeAIConfig",
    "DualeAIConnectionError",
    "DualeAIError",
    "DualeAISDK",
    "LibrariesClient",
    "LibraryCreateRequest",
    "LibraryDeleteRequest",
    "LibraryDocumentCreateResponse",
    "LibraryDocumentDeleteRequest",
    "LibraryDocumentGetRequest",
    "LibraryDocumentListRequest",
    "LibraryDocumentPage",
    "LibraryGetRequest",
    "LibraryListResponse",
    "LibraryPatchRequest",
    "LibraryResponseDocumentStatus",
    "LibraryUpdateRequest",
    "LibraryUploadError",
    "LibraryWithRevision",
    "MessagingError",
    "PreparedAttachment",
    "PublicIndexedDocument",
    "RedisCacheBackend",
    "ResponseFormat",
    "RoutingPolicy",
    "SQLiteCacheBackend",
    "SkillEnum",
    "TaskStoppedError",
    "Tool",
    "ToolContext",
    "ValidationError",
    "__version__",
    "activity",
    "ask",
    "configure_logging",
    "continue_conversation",
    "create_cache_backend",
    "create_sdk",
    "current_tool_context",
    "get_logger",
    "prepare_attachments",
    "tool",
    "uuid7",
]
