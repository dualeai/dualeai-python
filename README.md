# Duale AI Python SDK

`dualeai` is an async-first SDK for submitting managed Tasks, validating their results, and exposing Python functions
as customer-hosted Tools. Duale AI runs each Task; your application runs the Tools it registers.

**Status:** Public preview. Interfaces can change before a stable release.

<!-- Evidence: tests/test_tool_dispatch_invariants.py::TestServeOpensNoTaskStream::test_serve_makes_lifecycle_requests_only; tests/test_tool_dispatch_invariants.py::TestToolUseCarriesNoTaskIdentity::test_tool_use_event_has_no_task_id_field; tests/test_agent_lifecycle.py::test_tool_results_submission_resumes_after_triggering_tool_use_event. -->

## Choose a workflow

| Goal | Start here |
| --- | --- |
| Submit a Task and validate its result | [Run the first live Task](#run-the-first-live-task) |
| Stream a Task | [`streaming_minimal.py`](https://github.com/dualeai/dualeai-python/blob/main/examples/streaming_minimal.py) |
| Continue a Task | [`conversation_demo.py`](https://github.com/dualeai/dualeai-python/blob/main/examples/conversation_demo.py) |
| Stop work or cancel local observation | [Before production use](#before-production-use) |
| Publish and execute customer Tools | [`tool_execution.py`](https://github.com/dualeai/dualeai-python/blob/main/examples/tool_execution.py) |
| Attach a document or manage a persistent Library | [Document workflows](#document-workflows) |
| Add telemetry | [Optional telemetry instrumentation](#optional-telemetry-instrumentation) |
| Test without the Platform | [Offline testing](#test-without-the-platform) |

## Install

The distribution and import name are both `dualeai`. The SDK supports CPython 3.10 through 3.14 on Linux and macOS.
Every Task and Agent request and every Library metadata request goes through a Duale AI HPKE-protected API. Their
logical request and response contents are encrypted end to end between the SDK and the service over HTTPS. HPKE uses
each service's public key for encryption; your API token acts as the pre-shared key (PSK) to authenticate token
possession, as described by [RFC 9180 PSK mode](https://datatracker.ietf.org/doc/html/rfc9180.html#section-5.1.2).
The SDK does not call the Dashboard raw API. The Library service supplies signed HTTPS URLs for S3 part uploads; the
SDK sends those parts directly to the supplied URLs, outside HPKE. Optional telemetry and remote Redis use separate
connections.

<!-- Evidence: tests/test_http_transport_v3.py::test_real_v3_tls_boundary_reuses_discovery_per_service; tests/test_http_transport_v3.py::test_public_library_routes_and_bearer_stay_in_protected_logical_requests; tests/test_config.py::TestConfigLoading::test_default_config_values. The SDK follows the presigned upload URL without validating its scheme; no automated SDK test checks the service's HTTPS URL contract. Separate signed S3, telemetry, and Redis egress paths are a source-inspection finding without one cross-destination automated test. No automated test checks a deployed Duale AI endpoint or every advertised Python and OS combination. -->

```bash
python -m pip install dualeai
```

## Run the first live Task

An administrator must provision an API token and access to at least one configured model; the SDK creates neither. A
live request may consume metered or limited service resources, so use synthetic, non-sensitive input for the first
run. Set the token shown during provisioning for your current shell:

```bash
export DUALEAI_TOKEN=dualeai_your_provisioned_token_here
```

<!-- Provisioning, model eligibility, and service usage are Platform requirements; no automated SDK test enforces them. -->

Save this as `quickstart.py`:

```python
import asyncio

from pydantic import BaseModel

from dualeai import ask, create_sdk


class SupportDecision(BaseModel):
    next_action: str
    reason: str


async def main() -> None:
    async with create_sdk() as sdk:
        response = await ask(
            action="Review this synthetic support case and return the next safe action.",
            res=SupportDecision,
            sdk=sdk,
        )
        print(response.task_id)
        decision = await response.model()
        print(decision.next_action)


asyncio.run(main())
```

Run it with `python quickstart.py`. A completed run prints the Task ID followed by the validated `next_action`; the
exact values depend on the configured model. `ask()` returns an asynchronous response handle, and
`await response.model()` waits for its terminal result and validates it against `SupportDecision`.

<!-- Evidence: tests/test_feature_ask.py::TestUnitAskFunction::test_ask_returns_agent_response; tests/test_feature_results.py::TestUnitModelExtraction::test_model_validates_against_expected_type. No automated test executes this live quickstart or enforces its printed shape. -->

## Configure each workflow

`DUALEAI_TOKEN` is required for live Platform workflows. Other settings are conditional:

| Workflow | Requirement or destination |
| --- | --- |
| Task, Agent, and Library metadata | `DUALEAI_ENDPOINT` is the HTTPS Gateway base URL; it defaults to `https://api.duale.ai` |
| Tool publication and execution | `DUALEAI_AGENT_ID` identifies the provisioned Agent |
| Task attachments | `DUALEAI_TENANT_ID` and one Agent ID; configure `DUALEAI_AGENT_ID` or pass `agent_id=` when uploading |
| Persistent Libraries | `DUALEAI_TENANT_ID` and applicable Library access |
| Telemetry export | `DUALEAI_OBSERVABILITY__TOKEN` and, when needed, its separate `DUALEAI_OBSERVABILITY__ENDPOINT` |

See the [annotated environment example](https://github.com/dualeai/dualeai-python/blob/main/.env.example) for the
available settings.

<!-- Evidence: tests/test_config.py::TestConfigLoading::test_default_config_values; tests/test_agent_lifecycle.py::test_start_registers_manifest_and_first_heartbeat; tests/test_attachments.py::TestUploadAttachmentsAgentResolution::test_uses_configured_agent_id; tests/test_feature_observability.py::TestUnitObservabilityFeature::test_exporters_receive_per_signal_endpoints. No automated test runs these workflows against a deployed Duale AI endpoint. -->

### Protected API routing

`DUALEAI_ENDPOINT` supplies the Gateway base URL. With the default, the SDK sends each service's public-key discovery
GET and encrypted POST to these fixed endpoints. The encrypted requests use the `hpke-http/3` wire protocol:

| Service | Discovery GET and encrypted POST | Path inside the encrypted request |
| --- | --- | --- |
| HTTP Bridge | `/http-bridge/v1/hpke` | `/http-bridge/v1/hpke/tasks/{task_id}` and `/http-bridge/v1/hpke/agent/...` |
| Library | `/libraries/v1/hpke` | `/libraries/v1/hpke/tenants/{tenant_id}/...` |

Each service's discovery endpoint must return an HHKD v2 public-key record with a positive reuse lifetime. The SDK
shares that key across protected calls to the same service during its lifetime, so the first call uses a discovery GET
and an encrypted POST, while later calls use only encrypted POSTs. A call that needs a key after the lifetime ends
fetches a new one.

Both paths include the service prefix. The complete API token bytes are the PSK; SHA-512 of those bytes is the public
PSK ID. Each service's discovery response supplies a separate recipient public-key ID. Library bearer authorization
is a header inside the encrypted logical request.

A Library document-create receipt uses a different path: its `Location` header and body `location` identify the
document as `/v1/hpke/tenants/{tenant_id}/{library_id}/documents/{document_id}`. That response path is not the
`/libraries/v1/hpke` network endpoint. The SDK validates a synthetic response body in a local test; no automated
SDK test checks the header or a deployed service response.

<!-- Evidence: tests/test_http_transport_v3.py::test_real_v3_tls_boundary_reuses_discovery_per_service; tests/test_http_transport_v3.py::test_public_library_routes_and_bearer_stay_in_protected_logical_requests. The local TLS test checks one discovery GET and two protected POSTs per service during the lease. No automated SDK test forces lease expiry or checks the deployed Gateway routes or the receipt Location header. -->

## Document workflows

Task attachments and persistent Libraries are different workflows:

- A Task attachment is prepared, uploaded into a call-scoped Library, and included in that Task request. It needs a
  Tenant and one resolved Agent identifier. See
  [`document_upload.py`](https://github.com/dualeai/dualeai-python/blob/main/examples/document_upload.py).
- `sdk.libraries` manages persistent Libraries and their documents independently of a Task. Ordinary Library
  create/upload/read/delete operations need a Tenant but not an Agent identifier. See
  [`library_management.py`](https://github.com/dualeai/dualeai-python/blob/main/examples/library_management.py).

These SDK operations establish document upload and lifecycle state; they do not by themselves promise retrieval,
search, RAG, or a particular model's interpretation of document content.

<!-- Evidence: tests/test_attachments.py::TestUploadAttachmentsAgentResolution::test_uses_configured_agent_id; tests/test_http_transport_v3.py::test_task_create_fields_reach_the_hpke_session_with_wire_aliases; tests/test_libraries_client.py::test_public_library_surface_is_exported_and_stable_on_sdk; tests/test_http_transport_v3.py::test_public_library_routes_and_bearer_stay_in_protected_logical_requests. No automated test enforces the negative retrieval/search/RAG capability statement. -->

## Before production use

- **Streaming:** streamed deltas are a replaceable preview. Clear all accumulated preview text on
  `BridgeContentResetResponse`, and use the validated result from `response.model()` as authoritative.
- **Cancel vs Stop:** cancelling `response.task` interrupts local observation; it does not send a Platform Stop.
  `response.stop(reason)` sends a Stop request and confirms acceptance, not terminal completion.
- **Task identity:** store `response.task_id` in your own logs or records for correlation. The public SDK cannot look up
  or reattach to a Task by saved ID after its locally buffered events are unavailable.
- **Tool execution:** `serve()` publishes the Tool manifest and maintains Agent lifecycle state, but it opens no Task
  stream. Matching Tool calls execute on Task streams created by the same SDK instance.
- **Side effects:** Tool-call duplicate suppression is process-local and can expire. Reconcile state-changing actions
  against a durable application record using both Task and Tool-call identity.
- **Error disclosure:** without an `error_transform`, a Tool exception's class and message are sent in the model-facing
  error result. Keep secrets and personal data out of exception text, and use a failing-closed transform when redaction
  is required.
- **Failures:** Task and Library service failures surface typed SDK exceptions. When an authenticated response includes
  structured error details, the exception exposes them as `exc.problem_details`. Outer transport failures have no
  service error details. Handle these failures separately from local validation and cancellation.
- **Host integration:** creating an SDK replaces process-wide root logging handlers, and cleanup does not restore them.
  Optional OpenTelemetry integrations also affect process-global state. Applications that own either system should
  coordinate initialization and shutdown or reapply their configuration after constructing the SDK.

<!-- Evidence: tests/test_streaming_callbacks.py::TestStreamingCallbackWiring::test_reset_withdraws_failed_output_from_live_and_replay_views; tests/test_streaming_callbacks.py::TestTaskCancellation::test_task_cancel_propagates_to_bridge_iteration; tests/test_task_stop.py::test_response_stop_is_the_same_call_without_the_task_id; tests/test_feature_ask.py::TestUnitAskFunction::test_ask_returns_agent_response; tests/test_tool_dispatch_invariants.py::TestServeOpensNoTaskStream::test_serve_makes_lifecycle_requests_only; tests/test_agent_lifecycle.py::test_duplicate_tool_use_delivery_does_not_reexecute_customer_tool; tests/test_agent_lifecycle.py::test_registered_tool_retries_until_attempts_exhausted; tests/test_tools_pure.py::test_format_registered_tool_error_prefixes_and_bounds; tests/test_agent_lifecycle.py::test_tool_error_transform_redacts_model_facing_message; tests/test_streaming_callbacks.py::TestErrorCodeDispatch::test_error_code_is_preserved; tests/test_libraries_client.py::test_library_errors_are_translated_with_problem_details; tests/test_http_transport_v3.py::test_complete_logical_error_and_unchecked_failures_have_distinct_mapping. The absence of public lookup/reattach methods, duplicate-cache expiry/restart boundaries, root-handler replacement/non-restoration, and process-global telemetry ownership are source-inspection findings without exact automated tests. Stop settlement timing and cross-process side-effect durability are not established by local SDK tests. -->

## Optional telemetry instrumentation

The base package can export SDK traces and metrics. Install the extra to add automatic instrumentation for the
aiohttp client, Redis, SQLite3, and host/system metrics:

```bash
python -m pip install "dualeai[telemetry]"
```

Set `DUALEAI_OBSERVABILITY__TOKEN` and, when the default local collector is not appropriate,
`DUALEAI_OBSERVABILITY__ENDPOINT`. Then run a Task and verify that your collector receives its traces and metrics.
Installing the extra alone enables neither collection nor export.

When enabled, these integrations act process-wide and can observe host-application clients or system state, not only
operations initiated by `dualeai`. The SDK configures OTLP trace and metric export, not OTLP log export. Applications
that already own global OpenTelemetry providers or run multiple SDK instances should treat provider, instrumentation,
flush, and shutdown ownership as an integration decision rather than relying on per-instance isolation.

<!-- Evidence: tests/test_feature_observability.py::TestUnitObservabilityFeature::test_observability_disabled_by_default; tests/test_feature_observability.py::TestUnitObservabilityFeature::test_exporters_receive_per_signal_endpoints; tests/test_feature_observability.py::TestUnitObservabilityFeature::test_tracing_injects_context; tests/test_feature_observability.py::TestUnitObservabilityWiring::test_task_completion_invokes_task_completed_metric. Process-wide scope and the absence of an OTLP log exporter are source-inspection findings; no automated test enforces those exact statements. Multi-SDK provider and cleanup ownership are not established contracts. -->

## Test without the Platform

`MockSDK` provides keyed Task responses and an in-memory Library client for offline tests:

```python
import asyncio

from dualeai.testing import MockSDK


async def main() -> None:
    mock = MockSDK()
    mock.set_mock_response("Summarize invoice", {"result": "data"})
    result = await mock.mock_ask("Summarize invoice")
    print(result)


asyncio.run(main())
```

`mock_ask()` is a deterministic keyed lookup. It does not simulate the production Task transport, streaming, Tool
dispatch, or terminal failures.

<!-- Evidence: tests/test_mock_sdk.py::test_mock_sdk_returns_configured_response; tests/test_mock_sdk.py::test_mock_sdk_runs_library_workflow_without_a_connection. -->

## Project links

- [Examples](https://github.com/dualeai/dualeai-python/tree/main/examples)
- [Release notes](https://github.com/dualeai/dualeai-python/releases)
- [Issues](https://github.com/dualeai/dualeai-python/issues)
- [Contributing](https://github.com/dualeai/dualeai-python/blob/main/CONTRIBUTING.md)
- [Security policy](https://github.com/dualeai/dualeai-python/security/policy) — report suspected vulnerabilities through
  the private routes there, not through a public issue
- [Apache-2.0 license](https://github.com/dualeai/dualeai-python/blob/main/LICENSE)
