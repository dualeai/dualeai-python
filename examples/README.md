# Duale AI Python SDK examples

These files show one SDK workflow at a time. They are live, manually run examples: repository static checks inspect
their source, but CI does not execute their Platform behavior. Use a provisioned test environment and synthetic,
non-sensitive inputs because live Tasks, storage, and telemetry can consume service resources.

## Get the examples

The PyPI package does not install these source files. Clone the repository and install the checkout:

```bash
git clone https://github.com/dualeai/dualeai-python.git
cd dualeai-python
python -m pip install -e .
```

<!-- No automated test verifies the wheel's example-file inventory or executes these examples against the service. -->

## Configure access

Use CPython 3.10 through 3.14 on Linux or macOS. Set your provisioned token for the current shell:

```bash
export DUALEAI_TOKEN=dualeai_your_provisioned_token_here
```

Examples that submit Tasks also need access to at least one configured model. Tool-hosting examples need
`DUALEAI_AGENT_ID`; document examples list their additional requirements below. Set `DUALEAI_ENDPOINT` only when your
access instructions name a non-default HTTPS Gateway base URL.

<!-- Evidence: tests/test_config.py::TestConfigValidation::test_endpoint_format_validation; tests/test_http_transport_v3.py::test_sessions_use_service_protected_endpoints_and_existing_psk_identity. No automated test runs these examples against the service. -->

## Choose an example

The requirements column lists only what is needed beyond the shared token and, for Task examples, configured-model
access.

| Example | Extra requirement | Purpose |
| --- | --- | --- |
| [`conversation_demo.py`](conversation_demo.py) | None | Basic: continue one response and validate the continuation as a Pydantic model |
| [`simple_agent.py`](simple_agent.py) | Agent ID | Basic: publish a Tool manifest and maintain heartbeats; no Tool call executes |
| [`tool_execution.py`](tool_execution.py) | Agent ID | Basic: register one Tool and execute it on a Task stream opened by the same SDK |
| [`inventory_agent.py`](inventory_agent.py) | Agent ID | Advanced: sync and async Tools, side-effect safety, and error redaction |
| [`document_upload.py`](document_upload.py) | Tenant ID, Agent ID, synthetic file | Upload, ingest, attach, and submit one document with a Task |
| [`library_management.py`](library_management.py) | Tenant ID, Library access | Manage and clean up a persistent Library without an Agent |
| [`streaming_minimal.py`](streaming_minimal.py) | None | Basic: print a replaceable preview and a labeled final result |
| [`streaming_demo.py`](streaming_demo.py) | None | Record reset and completion metadata with structured logging |
| [`streaming_with_visualization.py`](streaming_with_visualization.py) | `rich` | Advanced: replace content in a Rich terminal display |
| [`streaming_web_ui_pattern.py`](streaming_web_ui_pattern.py) | None | Advanced: keep content and status state separate for WebSocket or SSE UIs |

Run the smallest example from the repository root:

```bash
python examples/streaming_minimal.py
```

The Rich example has one example-only dependency:

```bash
python -m pip install rich
```

Run the two document workflows as follows:

```bash
python examples/document_upload.py path/to/synthetic-document.pdf
python examples/library_management.py
```

`document_upload.py` creates a call-scoped Library for one Task and needs a resolved Agent identifier.
`library_management.py` operates an explicit persistent Library and needs no Agent identifier. Neither example claims
document search, retrieval/RAG, or a particular model's interpretation of embedded content.

<!-- Evidence: tests/test_attachments.py::TestUploadAttachmentsAgentResolution::test_uses_configured_agent_id; tests/test_libraries_client.py::test_upload_targets_explicit_library_and_returns_keyed_receipt; tests/test_libraries_client.py::test_wait_for_document_uses_fixed_interval_and_returns_terminal_state. -->

## Tool execution boundary

Task-only examples use `create_sdk()`, the concise factory that does not start hosted-Tool lifecycle work. Tool examples
construct `DualeAISDK` directly so they can register `@tool` callables on that exact instance before starting it. Direct
construction is also the path for dependency injection and advanced lifecycle configuration.

`serve()` publishes lifecycle state but opens no Task stream. A matching registered Tool executes only when a Task
opened by the same SDK instance receives its `tool.use` event. `simple_agent.py` demonstrates publication only;
`tool_execution.py` is the smallest complete execution path, and `inventory_agent.py` adds production concerns.

<!-- Evidence: tests/test_tool_dispatch_invariants.py::TestServeOpensNoTaskStream::test_serve_makes_lifecycle_requests_only; tests/test_tool_dispatch_invariants.py::TestToolUseCarriesNoTaskIdentity::test_tool_use_event_has_no_task_id_field; tests/test_agent_lifecycle.py::test_tool_results_submission_resumes_after_triggering_tool_use_event. -->

## Handle streaming replacement

Every streaming example treats deltas as a preview. `BridgeContentResetResponse` withdraws all content received before
it, so clear the buffer and build the replacement from later deltas. This is the core loop excerpt; see
[`streaming_minimal.py`](streaming_minimal.py) for response creation and final-result handling:

```python
from dualeai import BridgeContentResetResponse

buffer = []
async for event in response.stream():
    if isinstance(event, BridgeContentResetResponse):
        buffer.clear()
        continue
    buffer.append(event.delta)
```

Take the authoritative result from `await response.model()`, not from the accumulated preview; the two can differ.

<!-- Evidence: tests/test_streaming_callbacks.py::TestStreamingCallbackWiring::test_reset_withdraws_failed_output_from_live_and_replay_views. -->

## Project links

- [SDK package overview](https://github.com/dualeai/dualeai-python/blob/main/README.md)
- [Release notes](https://github.com/dualeai/dualeai-python/releases)
- [Issues](https://github.com/dualeai/dualeai-python/issues)
- [Security policy](https://github.com/dualeai/dualeai-python/security/policy)
