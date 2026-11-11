# Duale AI Python SDK examples

Run these examples manually in a provisioned test environment.

## Get the examples

The PyPI package does not install these source files. Clone the repository and install the checkout:

```bash
git clone https://github.com/dualeai/dualeai-python.git
cd dualeai-python
python -m pip install -e .
```

## Configure access

Use a Python version that the [package README](https://github.com/dualeai/dualeai-python/blob/main/README.md#install)
supports. Set your provisioned token for the current shell:

```bash
export DUALEAI_TOKEN=dualeai_your_provisioned_token_here
```

Examples that submit Tasks also need a model pool with at least one configured model. Tool-hosting examples need
`DUALEAI_AGENT_ID`; document examples list their additional requirements below. Set `DUALEAI_ENDPOINT` only when your
access instructions name another API origin.

## Choose an example

| Example | Extra requirement | Purpose |
| --- | --- | --- |
| [`streaming_minimal.py`](streaming_minimal.py) | None | Basic: print the streamed answer in order, then the final result |
| [`streaming_web_ui_pattern.py`](streaming_web_ui_pattern.py) | None | Advanced: keep ordered content and status state separate for WebSocket or SSE UIs |
| [`continuation_demo.py`](continuation_demo.py) | None | Basic: continue one response and validate the continuation as a Pydantic model |
| [`simple_agent.py`](simple_agent.py) | Agent ID | Basic: publish a Tool and keep it available; no Tool Call runs |
| [`tool_execution.py`](tool_execution.py) | Agent ID | Basic: serve one Tool and run it in a Task that the same SDK instance submits |
| [`inventory_agent.py`](inventory_agent.py) | Agent ID | Advanced: sync and async Tools, a Tool that is safe to call twice, and error redaction |
| [`document_upload.py`](document_upload.py) | Tenant ID, Agent ID, synthetic file, Library permissions below | Upload one document, wait until it is ready, and submit it with a Task |
| [`library_management.py`](library_management.py) | Tenant ID, Library permissions below | Create, upload to, and inspect a persistent Library without configuring an Agent ID |

Run the smallest example from the repository root:

```bash
python examples/streaming_minimal.py
```

## Order and replace streamed content

[Stream an answer](https://duale.ai/en/docs/sdk/streaming) gives the ordering and replacement rule for streamed text.
The console example [`streaming_minimal.py`](streaming_minimal.py) prints the corrected text again when a late chunk or
a reset changes text it already printed. [`streaming_web_ui_pattern.py`](streaming_web_ui_pattern.py) keeps the ordered
text in UI state.

## Where your Tools run

A registered Tool runs only in a Task that the same SDK instance submitted; [Authoring
tools](https://duale.ai/en/docs/sdk/tools) explains how Tool Calls reach your function.

## Run the document examples

Before you run either document workflow, get these permissions:

- `library:upload` on the Tenant
- `library:write` on the new Library's path, the resource `library:<path>`
- `library:read` on that path

The supplied Agent default grants them to an Agent Identity for Libraries under its own `agent/<agent_id>/` path.
`document_upload.py` creates its Library there when the token belongs to the Agent Identity named by `DUALEAI_AGENT_ID`.
`library_management.py` creates its Library under `examples/library-management/`, which the Agent default does not
cover: its caller needs `library:write` and `library:read` on `library:examples/library-management/*`.
[Library access](https://duale.ai/en/docs/libraries/access#what-each-action-allows) states what each action allows.

Both examples leave a Library and its document in the configured environment. Arrange cleanup with a person who can
delete that Library in the Dashboard: an API token cannot delete a whole Library.

Run the document workflows:

```bash
python examples/document_upload.py path/to/synthetic-document.pdf
python examples/library_management.py
```

## Project links

- [SDK documentation](https://duale.ai/en/docs/sdk)
- [Errors and reliability](https://duale.ai/en/docs/sdk/errors)
- [SDK package overview](https://github.com/dualeai/dualeai-python/blob/main/README.md)
- [Release notes](https://github.com/dualeai/dualeai-python/releases)
- [Issues](https://github.com/dualeai/dualeai-python/issues)
- [Security policy](https://github.com/dualeai/dualeai-python/security/policy)
