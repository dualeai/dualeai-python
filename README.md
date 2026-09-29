# Duale AI Python SDK

`dualeai` is an async Python SDK for submitting Tasks, validating their results, and exposing Python functions as
Tools. Duale AI runs each Task; your application runs the Tools it registers.

**Status:** Public preview. Interfaces can change before a stable release.

## Install

Use CPython 3.10 through 3.14 on Linux or macOS:

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

If the request fails, follow [Errors and reliability](https://duale.ai/en/docs/sdk/errors) to distinguish credential,
permission, service, and transport failures before retrying.

<!-- Evidence: tests/test_feature_ask.py::TestUnitAskFunction::test_ask_returns_agent_response; tests/test_feature_results.py::TestUnitModelExtraction::test_model_validates_against_expected_type. No automated test executes this live quickstart or enforces its printed shape. -->

## Configuration

`DUALEAI_TOKEN` is required for live requests. Set the other values only when your workflow needs them:

| Setting | When to use it |
| --- | --- |
| `DUALEAI_ENDPOINT` | A different API environment; the default is `https://api.duale.ai` |
| `DUALEAI_AGENT_ID` | Hosting Tools or uploading Task attachments |
| `DUALEAI_TENANT_ID` | Managing Libraries or uploading Task attachments |
| `DUALEAI_OBSERVABILITY__TOKEN` | Exporting telemetry, with a separate telemetry token |

For Task attachments, you can also pass `agent_id=` when uploading instead of setting `DUALEAI_AGENT_ID`.
Optional automatic instrumentation is available with `dualeai[telemetry]`; installation alone does not enable it.
See the [configuration example](https://github.com/dualeai/dualeai-python/blob/main/.env.example) for all settings,
including the telemetry destination.

<!-- Proof owners: tests/test_config.py, tests/test_attachments.py and tests/test_feature_observability.py. -->

## Examples and next steps

The [examples guide](https://github.com/dualeai/dualeai-python/tree/main/examples) lists setup and permissions for
each workflow. Document examples create stored Libraries; arrange their cleanup in the Dashboard before running them.

| What you want to do | Start here |
| --- | --- |
| Stream a response | [Streaming example](https://github.com/dualeai/dualeai-python/blob/main/examples/streaming_minimal.py) |
| Continue a conversation | [Conversation example](https://github.com/dualeai/dualeai-python/blob/main/examples/conversation_demo.py) |
| Execute Python functions as Tools | [Tool example](https://github.com/dualeai/dualeai-python/blob/main/examples/tool_execution.py) |
| Attach a document to a Task | [Attachment example](https://github.com/dualeai/dualeai-python/blob/main/examples/document_upload.py) |
| Manage a persistent Library | [Library example](https://github.com/dualeai/dualeai-python/blob/main/examples/library_management.py) |
| Handle failures and stopping | [Errors and reliability](https://duale.ai/en/docs/sdk/errors) |
| Test without a live connection | [MockSDK reference](https://duale.ai/en/docs/sdk/reference#mocksdk) |

## Integrate into an application

- **Results:** treat streamed text as a preview. Clear it on `BridgeContentResetResponse` and use
  `await response.model()` for the validated result.
- **Stopping:** cancelling `response.task` stops local observation. Use `await response.stop(reason)` to request a
  Platform Stop; its response confirms acceptance, not completion. Keep `response.task_id` for diagnosis; the SDK
  cannot reattach to a Task using only its saved ID.
- **Tools:** calls execute on Task streams opened by the same SDK instance that registered the functions. Protect
  side effects with durable duplicate detection and redact exception text with `error_transform`. The
  [inventory example](https://github.com/dualeai/dualeai-python/blob/main/examples/inventory_agent.py) demonstrates
  redaction and explains where your application must record side effects durably.
- **Logging and telemetry:** SDK initialization replaces the process's root logging handlers; cleanup does not restore
  them. Optional telemetry instrumentation also acts across the process. Coordinate these settings with your host
  application.

<!-- Proof owners: tests/test_streaming_callbacks.py, tests/test_task_stop.py, tests/test_tool_dispatch_invariants.py and tests/test_agent_lifecycle.py. Saved-ID reattachment limits and process-global logging/telemetry behavior are source-inspection findings without dedicated automated tests. Platform permissions and Library cleanup requirements belong to the service and are documented in examples/README.md. -->

## Project links

- [SDK documentation](https://duale.ai/en/docs/sdk)
- [Examples](https://github.com/dualeai/dualeai-python/tree/main/examples)
- [Release notes](https://github.com/dualeai/dualeai-python/releases)
- [Issues](https://github.com/dualeai/dualeai-python/issues)
- [Contributing](https://github.com/dualeai/dualeai-python/blob/main/CONTRIBUTING.md)
- [Security policy](https://github.com/dualeai/dualeai-python/security/policy) — report suspected vulnerabilities through
  the private routes there, not through a public issue
- [Apache-2.0 license](https://github.com/dualeai/dualeai-python/blob/main/LICENSE)
