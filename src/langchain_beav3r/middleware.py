from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, TypeAlias

from beav3r_sdk.client import Beav3r

JSON = dict[str, Any]

try:
    from langchain.agents.middleware import AgentMiddleware
    from langchain.messages import ToolMessage
    from langchain.tools.tool_node import ToolCallRequest
    from langgraph.types import Command
except ImportError as error:  # pragma: no cover - exercised in dependency-free installs
    AgentMiddleware = object  # type: ignore[assignment]
    ToolMessage = Any  # type: ignore[assignment]
    ToolCallRequest = Any  # type: ignore[assignment]
    Command = Any  # type: ignore[assignment]
    _LANGCHAIN_IMPORT_ERROR: Exception | None = error
else:
    _LANGCHAIN_IMPORT_ERROR = None


RequestBuilder: TypeAlias = Callable[[Any], Mapping[str, Any]]
ToolConfigValue: TypeAlias = "Beav3rToolConfig | str | bool"


class Beav3rApprovalPendingError(RuntimeError):
    def __init__(self, action_id: str, reason: str | None = None) -> None:
        self.action_id = action_id
        self.reason = reason
        message = reason or f"Beav3r approval is still pending for action {action_id}"
        super().__init__(message)


@dataclass(slots=True)
class Beav3rToolConfig:
    action_type: str | None = None
    payload_builder: RequestBuilder | None = None
    attributes_builder: RequestBuilder | None = None
    action_id_builder: Callable[[Any], str] | None = None
    poll_interval_ms: int | None = None
    timeout_ms: int | None = None


class Beav3rApprovalMiddleware(AgentMiddleware):
    def __init__(
        self,
        client: Beav3r,
        *,
        tool_configs: Mapping[str, ToolConfigValue] | None = None,
        protect_all_tools: bool = True,
        action_namespace: str | None = None,
        poll_interval_ms: int = 3000,
        timeout_ms: int = 5 * 60 * 1000,
    ) -> None:
        _require_langchain()
        super().__init__()
        self.client = client
        self.tool_configs = dict(tool_configs or {})
        self.protect_all_tools = protect_all_tools
        self.action_namespace = (action_namespace or "").strip(".")
        self.poll_interval_ms = poll_interval_ms
        self.timeout_ms = timeout_ms

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        config = self._resolve_config(request)
        if config is None:
            return handler(request)

        decision = self._authorize_tool_call(request, config)
        if decision is not None:
            return decision

        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        config = self._resolve_config(request)
        if config is None:
            return await handler(request)

        decision = await asyncio.to_thread(self._authorize_tool_call, request, config)
        if decision is not None:
            return decision

        return await handler(request)

    def _resolve_config(self, request: ToolCallRequest) -> Beav3rToolConfig | None:
        tool_name = self._tool_name(request)
        raw = self.tool_configs.get(tool_name)

        if raw is False:
            return None
        if raw is True:
            return Beav3rToolConfig()
        if isinstance(raw, str):
            return Beav3rToolConfig(action_type=raw)
        if isinstance(raw, Beav3rToolConfig):
            return raw
        if raw is None and self.protect_all_tools:
            return Beav3rToolConfig()
        return None

    def _authorize_tool_call(
        self,
        request: ToolCallRequest,
        config: Beav3rToolConfig,
    ) -> ToolMessage | Command | None:
        action = self._build_action_request(request, config)
        result = self.client.guard_and_wait(
            action,
            poll_interval_ms=config.poll_interval_ms or self.poll_interval_ms,
            timeout_ms=config.timeout_ms or self.timeout_ms,
        )
        status = str(result.get("status") or "")
        if status in {"approved", "executed"}:
            return None
        if status == "pending":
            raise Beav3rApprovalPendingError(
                str(result.get("actionId") or ""),
                result.get("reason"),
            )

        reason = result.get("reason") or "Blocked by Beav3r policy"
        action_id = str(result.get("actionId") or "")
        return ToolMessage(
            content=_blocked_tool_message(
                tool_name=self._tool_name(request),
                status=status or "blocked",
                reason=str(reason),
                action_id=action_id,
            ),
            tool_call_id=str(request.tool_call["id"]),
        )

    def _build_action_request(
        self,
        request: ToolCallRequest,
        config: Beav3rToolConfig,
    ) -> JSON:
        tool_name = self._tool_name(request)
        args = self._tool_args(request)
        payload = (
            dict(config.payload_builder(request))
            if config.payload_builder
            else _default_payload(args)
        )
        attributes = (
            dict(config.attributes_builder(request))
            if config.attributes_builder
            else _default_attributes(tool_name, args)
        )
        action: JSON = {
            "actionType": config.action_type or self._action_type_for(tool_name),
            "payload": payload,
            "attributes": attributes,
        }
        if config.action_id_builder is not None:
            action["actionId"] = config.action_id_builder(request)
        return action

    def _action_type_for(self, tool_name: str) -> str:
        if not self.action_namespace:
            return tool_name
        return f"{self.action_namespace}.{tool_name}"

    @staticmethod
    def _tool_name(request: ToolCallRequest) -> str:
        return str(request.tool_call["name"])

    @staticmethod
    def _tool_args(request: ToolCallRequest) -> Mapping[str, Any]:
        args = request.tool_call.get("args") or {}
        if isinstance(args, Mapping):
            return args
        return {"value": args}


def _default_payload(args: Mapping[str, Any]) -> JSON:
    return dict(args)


def _default_attributes(tool_name: str, args: Mapping[str, Any]) -> JSON:
    attributes: JSON = {"tool_name": tool_name}
    for key, value in args.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            attributes[str(key)] = value
    return attributes


def _blocked_tool_message(
    *,
    tool_name: str,
    status: str,
    reason: str,
    action_id: str,
) -> str:
    suffix = f" (actionId={action_id})" if action_id else ""
    return (
        f"Beav3r blocked tool `{tool_name}` with status `{status}`: {reason}{suffix}. "
        "Adjust the request, update policy, or approve it in Beav3r before retrying."
    )


def _require_langchain() -> None:
    if _LANGCHAIN_IMPORT_ERROR is None:
        return
    raise RuntimeError(
        "LangChain support requires langchain and langgraph plus the Beav3r LangChain "
        "adapter package. Install them with: pip install beav3r-sdk-langchain"
    ) from _LANGCHAIN_IMPORT_ERROR
