from __future__ import annotations

import os

from beav3r_sdk import Beav3r
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langchain_beav3r import (
    Beav3rApprovalMiddleware,
    Beav3rApprovalPendingError,
    Beav3rToolConfig,
)

DEFAULT_AGENT_ID = "langchain_demo"
DEFAULT_PAYMENT_AMOUNT = 25
DEFAULT_PAYMENT_RECIPIENT = "0x1111111111111111111111111111111111111111"
DEFAULT_MODEL_NAME = "gpt-4.1-mini"
DEFAULT_PROVIDER_BASE_URL = "https://api.openai.com/v1"


def send_usdt(amount: int, recipient: str) -> str:
    """Send a USDT payment."""

    return f"Sent {amount} USDT to {recipient}"


def main() -> None:
    base_url = os.environ["BEAV3R_BASE_URL"]
    api_key = os.environ["BEAV3R_API_KEY"]
    provider_api_key = os.environ.get("OPENAI_API_KEY") or os.environ["LLM_PROVIDER_API_KEY"]
    provider_base_url = (
        os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("LLM_PROVIDER_BASE_URL")
        or DEFAULT_PROVIDER_BASE_URL
    )
    model_name = os.environ.get("MODEL_NAME", DEFAULT_MODEL_NAME)
    agent_id = DEFAULT_AGENT_ID
    amount = DEFAULT_PAYMENT_AMOUNT
    recipient = DEFAULT_PAYMENT_RECIPIENT
    timeout_ms = int(os.environ.get("BEAV3R_TIMEOUT_MS", str(5 * 60 * 1000)))

    client = Beav3r(
        base_url=base_url,
        api_key=api_key,
        agent_id=agent_id,
    )
    model = ChatOpenAI(
        model=model_name,
        api_key=provider_api_key,
        base_url=provider_base_url,
        temperature=0,
    )

    agent = create_agent(
        model=model,
        tools=[send_usdt],
        middleware=[
            Beav3rApprovalMiddleware(
                client,
                tool_configs={
                    "send_usdt": Beav3rToolConfig(
                        action_type="payments.send_usdt",
                    ),
                },
                timeout_ms=timeout_ms,
            )
        ],
    )

    user_prompt = (
        f"Send {amount} USDT to {recipient}. "
        "Use the send_usdt tool. If the payment needs approval, say that clearly."
    )

    print(f"Beav3r server: {base_url}")
    print(f"Agent ID: {agent_id}")
    print(f"Model name: {model_name}")
    print(f"Provider base URL: {provider_base_url}")
    print("Expected Beav3r actionType: payments.send_usdt")
    print(f"Requested amount: {amount}")
    print(f"Requested recipient: {recipient}")

    try:
        result = agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": user_prompt,
                    }
                ]
            }
        )
    except Beav3rApprovalPendingError as error:
        print(
            "Beav3r received the tool call and it is waiting for human approval. "
            f"actionId={error.action_id}"
        )
        if error.reason:
            print(f"Reason: {error.reason}")
        return

    print(result)


if __name__ == "__main__":
    main()
