# Duale AI Python SDK Examples

Use these examples to inspect one SDK capability at a time.

## Prerequisites

Use Python 3.10 or newer. Install the package and set the API token:

```bash
python -m pip install dualeai
export DUALEAI_TOKEN="dualeai_your-token-here"
```

Hosted Tool examples also require `DUALEAI_AGENT_ID`. `document_upload.py` requires both `DUALEAI_AGENT_ID` and
`DUALEAI_TENANT_ID`. Examples that submit Task requests require a Tenant and an eligible model pool.

## Run the smallest example

Run the minimal streaming example from the SDK directory:

```bash
python examples/streaming_minimal.py
```

A successful run prints a streamed preview and then the final Task Result.

## Choose another example

| Area         | Example                           | Use it to                                                         |
| ------------ | --------------------------------- | ----------------------------------------------------------------- |
| Continuation | `conversation_demo.py`            | Submit a continuation Task request and validate structured output |
| Tools        | `simple_agent.py`                 | Host Tools for a provisioned Agent Identity                       |
| Tools        | `inventory_agent.py`              | Exercise typed sync and async Tools, redaction, and safeguards    |
| Documents    | `document_upload.py`              | Upload, ingest, and attach a document to a Task request           |
| Streaming    | `streaming_minimal.py`            | Apply the smallest correct streaming pattern                      |
| Streaming    | `streaming_demo.py`               | Add structured logging and content-event counts                   |
| Streaming    | `streaming_with_visualization.py` | Render replacement-aware output in a Rich terminal interface      |
| Streaming    | `streaming_web_ui_pattern.py`     | Separate streaming state for a WebSocket or server-sent event UI  |

Pass the document path to the upload example:

```bash
python examples/document_upload.py path/to/document.pdf
```

The Rich example also requires `rich`:

```bash
python -m pip install rich
```

## Handle streaming replacement once

Every streaming example follows the same rule: `BridgeContentResetResponse` withdraws all content received before it.
Clear the buffer, then build the replacement from later deltas.

```python
buffer = []
async for event in response.stream():
    if isinstance(event, BridgeContentResetResponse):
        buffer.clear()
        continue
    buffer.append(event.delta)
```

Take the final result from `await response.model()`, not from the accumulated preview. Most responses do not emit a
reset; duplicate text after one means the application did not clear all previously rendered content.

## Further reading

- [SDK package overview](../README.md)
- [SDK concepts](https://duale.ai/en/docs/sdk/concepts)
- [Authoring Tools](https://duale.ai/en/docs/sdk/tools)
- [Attach documents](https://duale.ai/en/docs/sdk/attachments)
- [API reference](https://duale.ai/en/docs/sdk/reference)
