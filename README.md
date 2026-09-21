# Duale AI Python SDK

Submit bounded AI agent work to the Duale AI managed runtime and receive typed results in Python. The SDK is not a
direct model-provider client.

**Status:** Public Preview (0.1.x). Interfaces can change before a stable release.

## Install

The distribution is named `dualeai`; import it as `dualeai`. Python 3.10 or newer is required.

```bash
python -m pip install dualeai
```

## Before the first request

Before you start, you need a Tenant, an API token, and an eligible model pool. The token identifies the Agent Identity
for a Task. Set that identity's public identifier in `DUALEAI_AGENT_ID` only when you host Tools.

Set `DUALEAI_TENANT_ID` for Library work. Every Library route takes the Tenant as a path segment, and task-scoped
attachments create a Library, so uploads need it too. `upload_attachments()` does not read `DUALEAI_AGENT_ID`: it takes
an `agent_id` keyword, and falls back to the sole agent registered on the SDK when exactly one is registered. Pass
`agent_id=sdk.agent_id` to route an upload through the configured identity.

Set the token shown during provisioning:

```bash
export DUALEAI_TOKEN=dualeai_your_token_here
```

Requests use `https://api.duale.ai` by default. Set `DUALEAI_ENDPOINT` only when your access instructions name another
environment.

## Submit a typed Task request

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
            action="Review this support case and return the next safe action.",
            res=SupportDecision,
            sdk=sdk,
        )
        print(response.task_id)
        decision = await response.model()
        print(decision.next_action)


asyncio.run(main())
```

Save the example as `quickstart.py`, then run it:

```bash
python quickstart.py
```

A successful run prints a Task identifier and the validated next action.

`ask()` returns an `AgentResponse` handle before the Platform has necessarily accepted the submission.
`await response.model()` waits for the observable outcome and validates a completed Task Result against
`SupportDecision`. Persist `response.task_id` before waiting so you can reconcile a transport or replay failure.

## Before live use

Three boundaries matter before live use:

- Streaming output can be replaced. Clear accumulated content on `BridgeContentResetResponse` and take the final result
  from `response.model()`.
- Customer Tool handling is not exactly once. For a state-changing Customer Tool, authorize each action and reconcile
  uncertain External Effects against a durable record.
- Uncaught Tool exception text can reach a model provider. Do not put credentials, personal data, or other secrets in
  exception messages.

## Next steps

- [Agent harness](https://duale.ai/en/docs/agent-harness) defines Agent, Task, Executor, Tool, Context, and the other
  runtime terms.
- [SDK concepts](https://duale.ai/en/docs/sdk/concepts) explains Task outcomes, routing, streaming, and continuation.
- [Authoring Tools](https://duale.ai/en/docs/sdk/tools) covers Tool publication, delivery, retries, identifiers, and
  model-facing errors.
- [Attachments](https://duale.ai/en/docs/sdk/attachments) and [Library
  management](https://duale.ai/en/docs/sdk/manage-libraries) cover documents.
- [Errors and reliability](https://duale.ai/en/docs/sdk/errors) defines typed failures and recovery.
- [API reference](https://duale.ai/en/docs/sdk/reference) lists the supported application-facing interfaces.

## Test without the Platform

`MockSDK` provides keyed responses and an in-memory Library client for local tests:

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

`mock_ask()` is a keyed lookup. It does not simulate the real Task transport, streaming, Tool dispatch, or terminal
errors.

## License

Apache-2.0.
