# beav3r-sdk-langchain

`beav3r-sdk-langchain` adds Beav3r approval enforcement to LangChain tool execution.

Use it when an agent can call tools that should be approved, denied, or delayed based on
Beav3r policy before the underlying tool actually runs.

## Requirements

- Python 3.10+
- `beav3r-sdk`
- `langchain`
- `langgraph`
- an LLM model provider package such as `langchain-openai`

## Install

### From PyPI

```bash
python3 -m pip install beav3r-sdk beav3r-sdk-langchain langchain-openai
```

### From source

Install the base SDK first, then this adapter:

```bash
cd ../beav3r-sdk-py
python3 -m pip install .

cd ../beav3r-sdk-langchain
python3 -m pip install .
python3 -m pip install langchain-openai
```

This integration is model-provider agnostic. It works with any LangChain-compatible LLM
provider as long as the agent can invoke tools through LangChain middleware. The examples in
this repository use OpenAI through `langchain-openai` as the default provider adapter.

Environment endpoints:

- Staging: `https://staging.server.beav3r.ai`
- Production: `https://server.beav3r.ai`

## Runtime requirements

From the agent process, you typically need:

- `BEAV3R_BASE_URL`
- `BEAV3R_API_KEY`
- credentials for the selected LLM model provider

For the included example script in this repository, that means:

- `OPENAI_API_KEY`
- optionally `OPENAI_BASE_URL`
- optionally `MODEL_NAME`

You do not need signer device credentials in the LangChain app for normal request-time
approval checks. Device credentials are only needed for signer-side flows such as approval
submission or signed device reads.

On the Beav3r side you still need:

- a running Beav3r server
- a project API key with permission to request actions
- policy rules that match the `actionType` and attributes you send
- at least one paired signer when a policy path requires human approval

## Quick start

```python
from __future__ import annotations

import os

from beav3r_sdk import Beav3r
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI

from langchain_beav3r import Beav3rApprovalMiddleware, Beav3rToolConfig


def send_usdt(amount: int, recipient: str) -> str:
    """Send a USDT payment."""

    return f"Sent {amount} USDT to {recipient}"


client = Beav3r(
    base_url=os.environ["BEAV3R_BASE_URL"],
    agent_id="langchain_demo",
    api_key=os.environ["BEAV3R_API_KEY"],
)

model = ChatOpenAI(
    model=os.environ.get("MODEL_NAME", "gpt-4.1-mini"),
    api_key=os.environ["OPENAI_API_KEY"],
    base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    temperature=0,
)

agent = create_agent(
    model=model,
    tools=[send_usdt],
    middleware=[
        Beav3rApprovalMiddleware(
            client,
            tool_configs={
                "send_usdt": Beav3rToolConfig(action_type="payments.send_usdt"),
            },
        )
    ],
)

result = agent.invoke(
    {
        "messages": [
            {
                "role": "user",
                "content": "Send 25 USDT to 0x1111111111111111111111111111111111111111.",
            }
        ]
    }
)

print(result)
```

The snippet above uses OpenAI via `ChatOpenAI`. The same middleware pattern applies to other
LangChain-compatible model providers, but the default example configuration targets OpenAI.

## Behavior specification

`Beav3rApprovalMiddleware` intercepts each LangChain tool call before execution.

For each protected tool call it sends a Beav3r action request with:

- `actionType`
- `payload`
- `attributes`
- optional `actionId`

This middleware remains compatibility-first: Beav3r decides whether execution is allowed,
and LangChain still runs the tool inline after an `approved` or `executed` result.

The default mapping is:

- `actionType`: the configured `Beav3rToolConfig.action_type`, otherwise the tool name, or
  `"{action_namespace}.{tool_name}"` if `action_namespace` is configured
- `payload`: the tool arguments
- `attributes`: `tool_name` plus scalar tool arguments

For example, this tool call:

```python
send_usdt(amount=25, recipient="0x1111...")
```

becomes this Beav3r request by default:

```python
{
    "actionType": "payments.send_usdt",
    "payload": {
        "amount": 25,
        "recipient": "0x1111..."
    },
    "attributes": {
        "tool_name": "send_usdt",
        "amount": 25,
        "recipient": "0x1111..."
    }
}
```

### Decision handling

- `approved` or `executed`: the original tool handler runs
- `denied`, `rejected`, `expired`, or any other non-approved final status: the middleware
  returns a `ToolMessage` explaining that the tool was blocked
- `pending`: the middleware raises `Beav3rApprovalPendingError`

### Executor-gated compatibility (optional)

If your runtime has a separate executor layer, you can forward Beav3r execution metadata from
`guard_and_wait` for downstream checks without changing default behavior.

Set one or both of:

- `authorization_metadata_key`: stores metadata on `request.tool_call[key]`
- `authorization_metadata_hook`: callback receiving `(request, metadata)`

The metadata includes available SDK fields such as:

- `status`
- `actionId`
- `actionHash`
- `evaluation`
- `reason`
- `pendingForMs`
- `executionAuthorizationArtifact`

## Configuration reference

`Beav3rApprovalMiddleware(...)` accepts:

- `client`: a configured `beav3r_sdk.Beav3r` client
- `tool_configs`: per-tool configuration map
- `protect_all_tools`: when `True`, every tool is protected unless explicitly disabled
- `action_namespace`: optional prefix used to derive default action types
- `poll_interval_ms`: polling interval passed to `guard_and_wait`
- `timeout_ms`: timeout passed to `guard_and_wait`
- `execution_auth_audience`: optional executor audience forwarded to SDK `guard_and_wait` (requires the underlying Beav3r API key to carry `actions:execute` for artifact minting)
- `authorization_metadata_key`: optional key for attaching Beav3r metadata to `request.tool_call`
- `authorization_metadata_hook`: optional callback for observing Beav3r metadata per guarded call

`Beav3rToolConfig(...)` accepts:

- `action_type`: explicit Beav3r action type
- `payload_builder`: callable that builds the Beav3r payload from the LangChain request
- `attributes_builder`: callable that builds the Beav3r attributes from the LangChain request
- `action_id_builder`: callable that supplies a custom Beav3r action id
- `poll_interval_ms`: per-tool polling override
- `timeout_ms`: per-tool timeout override
- `execution_auth_audience`: per-tool audience override for execution authorization artifact minting

### Protecting specific tools

```python
middleware = Beav3rApprovalMiddleware(
    client,
    protect_all_tools=False,
    tool_configs={
        "send_usdt": Beav3rToolConfig(action_type="payments.send_usdt"),
        "search_docs": False,
    },
)
```

### Metadata propagation example

```python
middleware = Beav3rApprovalMiddleware(
    client,
    authorization_metadata_key="beav3r_authz",
    authorization_metadata_hook=lambda request, metadata: print(metadata.get("actionHash")),
)
```

## Example agent

A runnable example lives in `examples/simple_agent.py`.

Run it after installing the base SDK, this package, and `langchain-openai`.
The example script defaults to OpenAI:

```bash
export BEAV3R_BASE_URL=https://staging.server.beav3r.ai
export BEAV3R_API_KEY=bvr_test_...
export OPENAI_API_KEY=...
# Optional overrides:
# export OPENAI_BASE_URL=https://api.openai.com/v1
# export MODEL_NAME=gpt-4.1-mini

python3 examples/simple_agent.py
```

If policy marks `payments.send_usdt` above a threshold as approval-gated, the middleware
will pause execution until Beav3r returns a final decision.

## Docker example

For an isolated local run without installing into the active Python environment:

```bash
export BEAV3R_BASE_URL=https://staging.server.beav3r.ai
export BEAV3R_API_KEY=bvr_test_...
export OPENAI_API_KEY=...
# Optional overrides:
# export OPENAI_BASE_URL=https://api.openai.com/v1
# export MODEL_NAME=gpt-4.1-mini

bash scripts/run_simple_agent_docker.sh
```

The script uses Python 3.11 in Docker, installs the sibling `beav3r-sdk-py` repository,
installs this adapter plus `langchain-openai`, and runs the example agent.
