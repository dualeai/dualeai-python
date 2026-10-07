# Duale AI Python SDK

Use `dualeai` to submit Tasks, validate results, and expose Python functions as Tools.
Duale AI runs the Tasks; your async Python application runs its registered Tools.

**Status:** Public preview. Interfaces can change before a stable release.

## Install

Use CPython 3.10 through 3.14 on Linux or macOS:

```bash
python -m pip install dualeai
```

## Run the first live Task

Ask your administrator for an API token and access to a configured model; the SDK creates neither.
Use synthetic, non-sensitive input: live requests can consume metered or limited service resources.
Set your provisioned token in the current shell:

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

Run `python quickstart.py`. A successful run prints the Task ID, then the validated `next_action`; values depend on the model.
`ask()` returns a response handle. `await response.model()` waits for the final Task result and validates it against
`SupportDecision`.

Before retrying a failure, use [Errors and reliability](https://duale.ai/en/docs/sdk/errors) to identify its cause.

<!-- Evidence: tests/test_feature_ask.py::TestUnitAskFunction::test_ask_returns_agent_response; tests/test_feature_results.py::TestUnitModelExtraction::test_model_validates_against_expected_type. No automated test executes this live quickstart or enforces its printed shape. -->

## Configuration

`DUALEAI_TOKEN` is required for live requests. Set the other values only when your workflow needs them:

| Setting | When to use it |
| --- | --- |
| `DUALEAI_ENDPOINT` | A different API environment; the default is `https://api.duale.ai` |
| `DUALEAI_AGENT_ID` | Hosting Tools or uploading Task attachments |
| `DUALEAI_TENANT_ID` | Managing Libraries or uploading Task attachments |
| `DUALEAI_OBSERVABILITY__TOKEN` | Exporting telemetry, with a separate telemetry token |

Attachment uploads also accept `agent_id=` in place of `DUALEAI_AGENT_ID`.
`dualeai[telemetry]` adds automatic instrumentation; configuration is still required to enable it.
See the [configuration example](https://github.com/dualeai/dualeai-python/blob/main/.env.example) for all settings.

<!-- Proof owners: tests/test_config.py, tests/test_attachments.py and tests/test_feature_observability.py. -->

## Examples and next steps

The [examples guide](https://github.com/dualeai/dualeai-python/tree/main/examples) lists setup and permissions.
Arrange Dashboard cleanup before running document examples: they leave stored Libraries.

For application tests without a live connection, use the
[MockSDK reference](https://duale.ai/en/docs/sdk/reference#mocksdk).

## Integrate into an application

- **Results:** streamed text is a live view of the answer and can arrive out of order. Order it by `generation` and
  `sequence` as the [examples guide](https://github.com/dualeai/dualeai-python/blob/main/examples/README.md#order-and-replace-streamed-content)
  shows, and use `await response.model()` for the validated result.
- **Stopping:** cancelling `response.task` stops local observation. Use `await response.stop(reason)` to request a
  Platform Stop. The response confirms acceptance of the Stop request, not that the Task has stopped.
  A Stop without the permission is refused at once with `DualeAIAuthError`. A permitted Stop is accepted even when
  the Task has already ended or the Platform does not know it.
  Keep `response.task_id` for diagnosis; the SDK cannot reattach to a Task using only its saved ID.
- **Tools:** calls execute on Task streams opened by the same SDK instance that registered the functions. Protect
  side effects with durable duplicate detection and redact exception text with `error_transform`. The
  [inventory example](https://github.com/dualeai/dualeai-python/blob/main/examples/inventory_agent.py) demonstrates
  redaction and explains where your application must record side effects durably.
- **Logging and telemetry:** SDK initialization replaces the process's root logging handlers; cleanup does not restore
  them. Optional telemetry instrumentation also acts across the process. Coordinate these settings with your host
  application.

<!-- Proof owners: tests/test_streaming_callbacks.py, tests/test_task_stop.py, tests/test_tool_dispatch_invariants.py and tests/test_agent_lifecycle.py. Saved-ID reattachment limits and process-global logging/telemetry behavior are source-inspection findings without dedicated automated tests. Platform permissions and Library cleanup requirements belong to the service and are documented in examples/README.md. Out-of-order content arrival is Platform behavior; no test in this repository enforces it. Acceptance of a permitted Stop for an ended or unknown Task is Platform behavior; no test in this repository enforces it. -->

## Project links

- [SDK documentation](https://duale.ai/en/docs/sdk)
- [Release notes](https://github.com/dualeai/dualeai-python/releases)
- [Issues](https://github.com/dualeai/dualeai-python/issues)
- [Contributing](https://github.com/dualeai/dualeai-python/blob/main/CONTRIBUTING.md)
- [Security policy](https://github.com/dualeai/dualeai-python/security/policy) — report suspected vulnerabilities through
  the private routes there, not through a public issue
- [Apache-2.0 license](https://github.com/dualeai/dualeai-python/blob/main/LICENSE)
