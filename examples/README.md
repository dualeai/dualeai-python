# Duale AI Python SDK examples

Run these examples manually in a provisioned test environment. Use synthetic, non-sensitive inputs: Tasks, storage,
and telemetry can consume service resources. CI checks the source but does not execute these live workflows.

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
access instructions name another HTTPS Gateway base URL.

<!-- Evidence: tests/test_config.py::TestConfigValidation::test_endpoint_format_validation; tests/test_http_transport_v3.py::test_real_v3_tls_boundary_reuses_discovery_per_service. No automated test runs these examples against the service. -->

## Choose an example

The table lists requirements beyond the token and, for Tasks, configured-model access.

| Example | Extra requirement | Purpose |
| --- | --- | --- |
| [`conversation_demo.py`](conversation_demo.py) | None | Basic: continue one response and validate the continuation as a Pydantic model |
| [`simple_agent.py`](simple_agent.py) | Agent ID | Basic: publish a Tool manifest and maintain heartbeats; no Tool call executes |
| [`tool_execution.py`](tool_execution.py) | Agent ID | Basic: register one Tool and execute it on a Task stream opened by the same SDK |
| [`inventory_agent.py`](inventory_agent.py) | Agent ID | Advanced: sync and async Tools, side-effect safety, and error redaction |
| [`document_upload.py`](document_upload.py) | Tenant ID, Agent ID, synthetic file, Library permissions below | Upload, ingest, attach, and submit one document with a Task |
| [`library_management.py`](library_management.py) | Tenant ID, Library permissions below | Create, upload to, and inspect a persistent Library without configuring an Agent ID |
| [`streaming_minimal.py`](streaming_minimal.py) | None | Basic: print the streamed answer in order, then the final result |
| [`streaming_web_ui_pattern.py`](streaming_web_ui_pattern.py) | None | Advanced: keep ordered content and status state separate for WebSocket or SSE UIs |

Run the smallest example from the repository root:

```bash
python examples/streaming_minimal.py
```

Before running either document workflow, obtain Tenant-scoped `library:upload`. Your existing access must let the
Platform grant `library:write` on the new Library. Both examples also need `library:read` to poll ingestion and inspect
documents.
Creation does not grant permissions the caller lacks. See
[Library access](https://duale.ai/en/docs/libraries/access) for the applicable policies and Grants.

Both examples leave a Library and its document in the configured environment. Arrange cleanup with a Human Identity who can
delete that Library in the Dashboard: whole-Library deletion requires `library:delete` and valid `aal3` authentication.
The SDK's API-token session has AAL2 and cannot perform that deletion.

Run the document workflows as follows:

```bash
python examples/document_upload.py path/to/synthetic-document.pdf
python examples/library_management.py
```

These examples do not verify search, retrieval/RAG, or model interpretation.

<!-- Evidence: tests/test_attachments.py::TestUploadAttachmentsAgentResolution::test_uses_configured_agent_id; tests/test_libraries_client.py::test_upload_targets_explicit_library_and_returns_keyed_receipt; tests/test_libraries_client.py::test_wait_for_document_uses_fixed_interval_and_returns_terminal_state. Library permissions and the AAL3 deletion requirement are Platform contracts; no automated SDK test enforces them. -->

## Tool execution boundary

Task-only examples use `create_sdk()`, which leaves Tool hosting stopped. Tool examples use `DualeAISDK` to register
functions before startup. Use the constructor for dependency injection and advanced lifecycle settings too.

`serve()` opens no Task stream. A registered Tool executes when the same SDK instance receives its `tool.use` event
on a Task stream.

<!-- Evidence: tests/test_tool_dispatch_invariants.py::TestServeOpensNoTaskStream::test_serve_makes_lifecycle_requests_only; tests/test_tool_dispatch_invariants.py::TestToolUseCarriesNoTaskIdentity::test_tool_use_event_has_no_task_id_field; tests/test_agent_lifecycle.py::test_tool_results_submission_resumes_after_triggering_tool_use_event. -->

## Order and replace streamed content

Streamed content is a live view of the answer while the Platform writes it, and chunks can arrive out of order. Every
`BridgeContentDeltaResponse` carries `generation` and `sequence`; a `BridgeContentResetResponse` carries the
`generation` it starts:

1. Keep only events from the highest `generation`; a higher generation replaces everything from earlier ones.
2. Display that generation's deltas sorted by `sequence`.

`response.stream()` yields events in arrival order. A replay after completion repeats every event, resets included.
Take the final answer from `await response.model()`. It can differ from the streamed text and, for an answer with no
format or the `text` format, it is the only copy that can carry the
[content mark](https://duale.ai/en/docs/content-marking). Only free-text answers stream: pass no `res` or
`response_format`, or use the `text` or `markdown` format.
[`streaming_web_ui_pattern.py`](streaming_web_ui_pattern.py) keeps the ordered text in UI state. The terminal-only
[`streaming_minimal.py`](streaming_minimal.py) prints the corrected text again when a late chunk or a reset changes text
it already printed.

<!-- Evidence: tests/test_sse_parser.py::TestSSEParserEvents::test_parser_accepts_the_frozen_content_delta_wire_event; tests/test_sse_parser.py::TestSSEParserEvents::test_parse_sse_preserves_content_replacement; tests/test_streaming_callbacks.py::TestStreamingCallbackWiring::test_replay_repeats_every_content_event_in_arrival_order. No automated test runs the ordering rule in these examples. Out-of-order arrival, the free-text rule and the content mark are Platform behavior; no test in this repository enforces them. -->

## Project links

- [SDK documentation](https://duale.ai/en/docs/sdk)
- [Errors and reliability](https://duale.ai/en/docs/sdk/errors)
- [SDK package overview](https://github.com/dualeai/dualeai-python/blob/main/README.md)
- [Release notes](https://github.com/dualeai/dualeai-python/releases)
- [Issues](https://github.com/dualeai/dualeai-python/issues)
- [Security policy](https://github.com/dualeai/dualeai-python/security/policy)
