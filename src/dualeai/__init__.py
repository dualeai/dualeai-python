"""Duale AI SDK - HTTP Bridge Transport (RFC-051)."""

# Type hints
from typing import TypedDict

from pydantic import BaseModel, ConfigDict
from typing_extensions import Unpack

# UUID utilities
from uuid_utils import uuid7

# Attachments (RFC-113 Library upload flow)
from dualeai.attachments import PreparedAttachment, prepare_attachments

# Cache backends
from dualeai.cache import CacheBackend, CacheConfig, RedisCacheBackend, SQLiteCacheBackend, create_cache_backend

# Configuration
from dualeai.config import DualeAIConfig
from dualeai.constants import TimingDefaults
from dualeai.decorators import (
    activity,
    agent,
    tool,
)

# Introspection helpers (get_*_metadata / get_*_registry) are intentionally not
# re-exported here; they are internal. Import from ``dualeai.decorators`` if needed.
# Exceptions
from dualeai.exceptions import (
    ActivityTimeoutError,
    AgentRegistrationError,
    BusinessError,
    CacheConnectionError,
    CacheError,
    CacheSerializationError,
    ConfigurationError,
    DualeAIAuthError,
    DualeAIConnectionError,
    DualeAIError,
    LibraryUploadError,
    MessagingError,
    RoutingError,
    TaskStoppedError,
    TaskSubmissionError,
    TaskTimeoutError,
    ValidationError,
)

# Libraries (RFC-113)
from dualeai.libraries import LibrariesClient

# Logging
from dualeai.logging_config import configure_logging, get_logger

# Bridge models (RFC-051)
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
    """Type definition for SDK configuration keyword arguments (RFC-051).

    Configuration via environment variables:
    - DUALEAI_TOKEN: API token for HTTP bridge authentication; starts with dualeai_ [required]
    - DUALEAI_ENDPOINT: HTTP bridge endpoint URL [optional]
    - DUALEAI_TENANT_ID: Tenant path segment for Library operations [optional]
    - DUALEAI_AGENT_ID: Provisioned identity for hosted tools and task attachments [optional]
    - DUALEAI_REDIS_URL: Redis server URL for caching [optional]
    """

    token: str  # API token for HTTP bridge; starts with dualeai_
    endpoint: str  # HTTP bridge endpoint URL
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
    """Create a DualeAISDK instance with optional configuration.

    Args:
        **kwargs: Configuration overrides for DualeAIConfig (supports SDKConfigKwargs)

    Returns:
        DualeAISDK instance
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
    "ActivityTimeoutError",
    "AgentRegistrationError",
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
    "CacheConnectionError",
    "CacheError",
    "CacheSerializationError",
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
    "RoutingError",
    "RoutingPolicy",
    "SQLiteCacheBackend",
    "SkillEnum",
    "TaskStoppedError",
    "TaskSubmissionError",
    "TaskTimeoutError",
    "Tool",
    "ToolContext",
    "ValidationError",
    "__version__",
    "activity",
    "agent",
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
