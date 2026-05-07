from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))


class Beav3rLangChainMiddlewareTests(unittest.TestCase):
    def setUp(self) -> None:
        self._created_modules = install_dependency_stubs()
        for name in [
            "langchain_beav3r",
            "langchain_beav3r.middleware",
        ]:
            sys.modules.pop(name, None)

    def tearDown(self) -> None:
        for name in self._created_modules:
            sys.modules.pop(name, None)
        for name in [
            "langchain_beav3r",
            "langchain_beav3r.middleware",
        ]:
            sys.modules.pop(name, None)

    def test_blocks_denied_tool_call(self) -> None:
        from beav3r_sdk.client import Beav3r
        from langchain.tools.tool_node import ToolCallRequest
        from langchain_beav3r import Beav3rApprovalMiddleware

        seen: dict[str, object] = {}

        def transport(url: str, method: str, headers: dict[str, str], body: bytes | None):
            seen["url"] = url
            seen["method"] = method
            seen["body"] = json.loads((body or b"{}").decode("utf-8"))
            return {
                "status": 200,
                "headers": {},
                "text": json.dumps(
                    {
                        "status": "denied",
                        "actionId": "act_blocked",
                        "reason": "amount above approval limit",
                    }
                ),
            }

        middleware = Beav3rApprovalMiddleware(
            Beav3r(base_url="http://beav3r.test", transport=transport),
            action_namespace="payments",
        )
        request = ToolCallRequest(
            tool_call={
                "id": "call_1",
                "name": "send_usdt",
                "args": {"amount": 25, "recipient": "0xabc"},
            }
        )

        result = middleware.wrap_tool_call(
            request,
            lambda _request: self.fail("tool handler should not execute"),
        )

        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["url"], "http://beav3r.test/actions/request")
        self.assertEqual(result.tool_call_id, "call_1")
        self.assertIn("amount above approval limit", result.content)
        self.assertEqual(seen["body"]["actionType"], "payments.send_usdt")
        self.assertEqual(seen["body"]["payload"], {"amount": 25, "recipient": "0xabc"})
        self.assertEqual(
            seen["body"]["attributes"],
            {
                "tool_name": "send_usdt",
                "amount": 25,
                "recipient": "0xabc",
            },
        )

    def test_allows_approved_tool_call(self) -> None:
        from beav3r_sdk.client import Beav3r
        from langchain.tools.tool_node import ToolCallRequest
        from langchain_beav3r import Beav3rApprovalMiddleware, Beav3rToolConfig

        def transport(url: str, method: str, headers: dict[str, str], body: bytes | None):
            return {
                "status": 200,
                "headers": {},
                "text": json.dumps(
                    {
                        "status": "approved",
                        "actionId": "act_ok",
                        "actionHash": "hash_ok",
                    }
                ),
            }

        middleware = Beav3rApprovalMiddleware(
            Beav3r(base_url="http://beav3r.test", transport=transport),
            tool_configs={"deploy_service": Beav3rToolConfig(action_type="ops.deploy")},
            protect_all_tools=False,
        )
        request = ToolCallRequest(
            tool_call={
                "id": "call_2",
                "name": "deploy_service",
                "args": {"service": "checkout", "environment": "production"},
            }
        )

        result = middleware.wrap_tool_call(request, lambda _request: "tool ran")

        self.assertEqual(result, "tool ran")

    def test_exposes_authorization_metadata_for_executor_checks(self) -> None:
        from beav3r_sdk.client import Beav3r
        from langchain.tools.tool_node import ToolCallRequest
        from langchain_beav3r import Beav3rApprovalMiddleware

        captured: dict[str, object] = {}

        def transport(url: str, method: str, headers: dict[str, str], body: bytes | None):
            return {
                "status": 200,
                "headers": {},
                "text": json.dumps(
                    {
                        "status": "approved",
                        "actionId": "act_exec_gate",
                        "actionHash": "hash_exec_gate",
                        "executionAuthorizationArtifact": {
                            "artifactId": "artifact_exec_gate",
                            "actionHash": "hash_exec_gate",
                        },
                        "evaluation": {
                            "decision": "require_approval",
                            "severity": "elevated",
                            "reason": "manual review",
                        },
                    }
                ),
            }

        request = ToolCallRequest(
            tool_call={
                "id": "call_meta",
                "name": "run_sensitive_tool",
                "args": {"target": "prod"},
            }
        )
        middleware = Beav3rApprovalMiddleware(
            Beav3r(base_url="http://beav3r.test", transport=transport),
            authorization_metadata_key="beav3r_authz",
            authorization_metadata_hook=lambda _req, metadata: captured.__setitem__(
                "metadata", dict(metadata)
            ),
        )

        def handler(req):
            captured["request_metadata"] = req.tool_call["beav3r_authz"]
            return "tool ran"

        result = middleware.wrap_tool_call(request, handler)

        self.assertEqual(result, "tool ran")
        self.assertEqual(
            captured["request_metadata"],
            {
                "status": "approved",
                "actionId": "act_exec_gate",
                "actionHash": "hash_exec_gate",
                "executionAuthorizationArtifact": {
                    "artifactId": "artifact_exec_gate",
                    "actionHash": "hash_exec_gate",
                },
                "evaluation": {
                    "decision": "require_approval",
                    "severity": "elevated",
                    "reason": "manual review",
                },
            },
        )
        self.assertEqual(captured["metadata"], captured["request_metadata"])

    def test_approve_deny_pending_behavior_unchanged(self) -> None:
        from beav3r_sdk.client import Beav3r
        from langchain.tools.tool_node import ToolCallRequest
        from langchain_beav3r import Beav3rApprovalMiddleware, Beav3rApprovalPendingError

        scenarios = [
            ("approved", "tool ran", None),
            ("denied", "blocked", None),
            ("pending", "pending", Beav3rApprovalPendingError),
        ]

        for status, expected, expected_error in scenarios:
            with self.subTest(status=status):
                def transport(url: str, method: str, headers: dict[str, str], body: bytes | None):
                    return {
                        "status": 200,
                        "headers": {},
                        "text": json.dumps(
                            {
                                "status": status,
                                "actionId": f"act_{status}",
                                "actionHash": f"hash_{status}",
                                "reason": f"reason_{status}",
                            }
                        ),
                    }

                middleware = Beav3rApprovalMiddleware(
                    Beav3r(base_url="http://beav3r.test", transport=transport),
                    execution_auth_audience="payments-executor",
                )
                request = ToolCallRequest(
                    tool_call={
                        "id": f"call_{status}",
                        "name": "send_usdt",
                        "args": {"amount": 25},
                    }
                )

                if expected_error is not None:
                    with self.assertRaises(expected_error):
                        middleware.wrap_tool_call(request, lambda _request: "tool ran")
                    continue

                result = middleware.wrap_tool_call(
                    request,
                    lambda _request: "tool ran",
                )
                if expected == "blocked":
                    self.assertEqual(result.tool_call_id, f"call_{status}")
                    self.assertIn("Beav3r blocked tool", result.content)
                else:
                    self.assertEqual(result, expected)

    def test_passes_execution_auth_audience_when_configured(self) -> None:
        from beav3r_sdk.client import Beav3r
        from langchain.tools.tool_node import ToolCallRequest
        from langchain_beav3r import Beav3rApprovalMiddleware, Beav3rToolConfig

        def transport(url: str, method: str, headers: dict[str, str], body: bytes | None):
            return {
                "status": 200,
                "headers": {},
                "text": json.dumps(
                    {
                        "status": "approved",
                        "actionId": "act_audience",
                        "actionHash": "hash_audience",
                    }
                ),
            }

        client = Beav3r(base_url="http://beav3r.test", transport=transport)
        middleware = Beav3rApprovalMiddleware(
            client,
            execution_auth_audience="payments-executor",
            tool_configs={
                "send_usdt": Beav3rToolConfig(
                    execution_auth_audience="payments-executor-overridden"
                )
            },
        )
        request = ToolCallRequest(
            tool_call={
                "id": "call_audience",
                "name": "send_usdt",
                "args": {"amount": 25},
            }
        )

        result = middleware.wrap_tool_call(request, lambda _request: "tool ran")

        self.assertEqual(result, "tool ran")
        self.assertEqual(
            client.last_guard_and_wait_kwargs,
            {
                "poll_interval_ms": 3000,
                "timeout_ms": 5 * 60 * 1000,
                "execution_auth_audience": "payments-executor-overridden",
                "audience": None,
            },
        )

    def test_does_not_break_legacy_guard_and_wait_signature(self) -> None:
        from langchain.tools.tool_node import ToolCallRequest
        from langchain_beav3r import Beav3rApprovalMiddleware

        class LegacyClient:
            def guard_and_wait(
                self,
                input: dict[str, object],
                *,
                poll_interval_ms: int = 3000,
                timeout_ms: int = 5 * 60 * 1000,
            ) -> dict[str, object]:
                return {
                    "status": "approved",
                    "actionId": "act_legacy",
                    "actionHash": "hash_legacy",
                }

        middleware = Beav3rApprovalMiddleware(
            LegacyClient(),  # type: ignore[arg-type]
            execution_auth_audience="payments-executor",
        )
        request = ToolCallRequest(
            tool_call={
                "id": "call_legacy",
                "name": "send_usdt",
                "args": {"amount": 1},
            }
        )

        result = middleware.wrap_tool_call(request, lambda _request: "tool ran")

        self.assertEqual(result, "tool ran")

    def test_skips_unconfigured_tool_when_protection_disabled(self) -> None:
        from beav3r_sdk.client import Beav3r
        from langchain.tools.tool_node import ToolCallRequest
        from langchain_beav3r import Beav3rApprovalMiddleware

        called = {"transport": False, "handler": False}

        def transport(url: str, method: str, headers: dict[str, str], body: bytes | None):
            called["transport"] = True
            return {"status": 200, "headers": {}, "text": "{}"}

        middleware = Beav3rApprovalMiddleware(
            Beav3r(base_url="http://beav3r.test", transport=transport),
            protect_all_tools=False,
        )
        request = ToolCallRequest(
            tool_call={
                "id": "call_3",
                "name": "search_docs",
                "args": {"query": "beav3r"},
            }
        )

        result = middleware.wrap_tool_call(
            request,
            lambda _request: called.__setitem__("handler", True) or "skipped",
        )

        self.assertFalse(called["transport"])
        self.assertTrue(called["handler"])
        self.assertEqual(result, "skipped")

    def test_raises_pending_error_when_approval_is_still_pending(self) -> None:
        from beav3r_sdk.client import Beav3r
        from langchain.tools.tool_node import ToolCallRequest
        from langchain_beav3r import Beav3rApprovalMiddleware, Beav3rApprovalPendingError

        def transport(url: str, method: str, headers: dict[str, str], body: bytes | None):
            return {
                "status": 200,
                "headers": {},
                "text": json.dumps(
                    {
                        "status": "pending",
                        "actionId": "act_waiting",
                        "reason": "Waiting for approver",
                    }
                ),
            }

        middleware = Beav3rApprovalMiddleware(
            Beav3r(base_url="http://beav3r.test", transport=transport),
        )
        request = ToolCallRequest(
            tool_call={
                "id": "call_4",
                "name": "deploy_service",
                "args": {"service": "checkout"},
            }
        )

        with self.assertRaises(Beav3rApprovalPendingError) as context:
            middleware.wrap_tool_call(request, lambda _request: "tool ran")

        self.assertEqual(context.exception.action_id, "act_waiting")
        self.assertEqual(context.exception.reason, "Waiting for approver")

    def test_custom_builders_override_default_request_shape(self) -> None:
        from beav3r_sdk.client import Beav3r
        from langchain.tools.tool_node import ToolCallRequest
        from langchain_beav3r import Beav3rApprovalMiddleware, Beav3rToolConfig

        seen: dict[str, object] = {}

        def transport(url: str, method: str, headers: dict[str, str], body: bytes | None):
            seen["body"] = json.loads((body or b"{}").decode("utf-8"))
            return {
                "status": 200,
                "headers": {},
                "text": json.dumps(
                    {
                        "status": "approved",
                        "actionId": "act_custom",
                    }
                ),
            }

        request = ToolCallRequest(
            tool_call={
                "id": "call_5",
                "name": "deploy_service",
                "args": {"service": "checkout", "environment": "production"},
            }
        )
        middleware = Beav3rApprovalMiddleware(
            Beav3r(base_url="http://beav3r.test", transport=transport),
            protect_all_tools=False,
            tool_configs={
                "deploy_service": Beav3rToolConfig(
                    action_type="ops.deploy",
                    payload_builder=lambda req: {
                        "target": req.tool_call["args"]["service"],
                    },
                    attributes_builder=lambda req: {
                        "environment": req.tool_call["args"]["environment"],
                        "tool_name": req.tool_call["name"],
                    },
                    action_id_builder=lambda req: f"manual_{req.tool_call['id']}",
                    poll_interval_ms=250,
                    timeout_ms=1_000,
                )
            },
        )

        result = middleware.wrap_tool_call(request, lambda _request: "tool ran")

        self.assertEqual(result, "tool ran")
        self.assertEqual(
            seen["body"],
            {
                "actionId": "manual_call_5",
                "agentId": "agent_default",
                "actionType": "ops.deploy",
                "payload": {"target": "checkout"},
                "attributes": {
                    "environment": "production",
                    "tool_name": "deploy_service",
                },
                "timestamp": 1_700_000_001,
                "nonce": "nonce_test",
                "expiry": 4_100_000_000,
            },
        )

    def test_non_mapping_arguments_are_wrapped_in_value_payload(self) -> None:
        from beav3r_sdk.client import Beav3r
        from langchain.tools.tool_node import ToolCallRequest
        from langchain_beav3r import Beav3rApprovalMiddleware

        seen: dict[str, object] = {}

        def transport(url: str, method: str, headers: dict[str, str], body: bytes | None):
            seen["body"] = json.loads((body or b"{}").decode("utf-8"))
            return {
                "status": 200,
                "headers": {},
                "text": json.dumps(
                    {
                        "status": "denied",
                        "actionId": "act_value",
                        "reason": "Scalar input blocked",
                    }
                ),
            }

        middleware = Beav3rApprovalMiddleware(
            Beav3r(base_url="http://beav3r.test", transport=transport),
        )
        request = ToolCallRequest(
            tool_call={
                "id": "call_6",
                "name": "set_priority",
                "args": "urgent",
            }
        )

        result = middleware.wrap_tool_call(request, lambda _request: "tool ran")

        self.assertEqual(seen["body"]["payload"], {"value": "urgent"})
        self.assertEqual(
            seen["body"]["attributes"],
            {
                "tool_name": "set_priority",
                "value": "urgent",
            },
        )
        self.assertEqual(result.tool_call_id, "call_6")
        self.assertIn("Scalar input blocked", result.content)

    def test_async_wrapper_returns_handler_result_for_approved_call(self) -> None:
        from beav3r_sdk.client import Beav3r
        from langchain.tools.tool_node import ToolCallRequest
        from langchain_beav3r import Beav3rApprovalMiddleware

        def transport(url: str, method: str, headers: dict[str, str], body: bytes | None):
            return {
                "status": 200,
                "headers": {},
                "text": json.dumps(
                    {
                        "status": "approved",
                        "actionId": "act_async",
                    }
                ),
            }

        middleware = Beav3rApprovalMiddleware(
            Beav3r(base_url="http://beav3r.test", transport=transport),
        )
        request = ToolCallRequest(
            tool_call={
                "id": "call_7",
                "name": "search_docs",
                "args": {"query": "approval middleware"},
            }
        )

        async def handler(_request):
            return "async tool ran"

        result = asyncio.run(middleware.awrap_tool_call(request, handler))

        self.assertEqual(result, "async tool ran")


def install_dependency_stubs() -> list[str]:
    created: list[str] = []

    beav3r_client_module = types.ModuleType("beav3r_sdk.client")

    class Beav3r:
        def __init__(
            self,
            *,
            base_url: str,
            agent_id: str | None = None,
            api_key: str | None = None,
            default_expiry_seconds: int = 60,
            transport=None,
        ) -> None:
            self.base_url = base_url.rstrip("/")
            self.agent_id = agent_id
            self.api_key = api_key
            self.default_expiry_seconds = default_expiry_seconds
            self.transport = transport
            self.last_guard_and_wait_kwargs: dict[str, object] | None = None

        def guard_and_wait(
            self,
            input: dict[str, object],
            *,
            poll_interval_ms: int = 3000,
            timeout_ms: int = 5 * 60 * 1000,
            execution_auth_audience: str | None = None,
            audience: str | None = None,
        ) -> dict[str, object]:
            self.last_guard_and_wait_kwargs = {
                "poll_interval_ms": poll_interval_ms,
                "timeout_ms": timeout_ms,
                "execution_auth_audience": execution_auth_audience,
                "audience": audience,
            }
            body = {
                "actionId": input.get("actionId") or "generated_action",
                "agentId": input.get("agentId") or self.agent_id or "agent_default",
                "actionType": input["actionType"],
                "payload": input["payload"],
                "attributes": input.get("attributes") or {},
                "timestamp": 1_700_000_001,
                "nonce": "nonce_test",
                "expiry": 4_100_000_000,
            }
            response = self.transport(
                f"{self.base_url}/actions/request",
                "POST",
                {"content-type": "application/json"},
                json.dumps(body).encode("utf-8"),
            )
            return json.loads(response["text"])

    beav3r_client_module.Beav3r = Beav3r

    langchain_module = types.ModuleType("langchain")
    agents_module = types.ModuleType("langchain.agents")
    middleware_module = types.ModuleType("langchain.agents.middleware")
    tools_module = types.ModuleType("langchain.tools")
    tool_node_module = types.ModuleType("langchain.tools.tool_node")
    messages_module = types.ModuleType("langchain.messages")
    langgraph_module = types.ModuleType("langgraph")
    langgraph_types_module = types.ModuleType("langgraph.types")

    class AgentMiddleware:
        pass

    class ToolMessage:
        def __init__(self, content: str, tool_call_id: str) -> None:
            self.content = content
            self.tool_call_id = tool_call_id

    class ToolCallRequest:
        def __init__(self, *, tool_call: dict[str, object]) -> None:
            self.tool_call = tool_call

    class Command:
        def __init__(self, update: dict[str, object] | None = None) -> None:
            self.update = update or {}

    middleware_module.AgentMiddleware = AgentMiddleware
    messages_module.ToolMessage = ToolMessage
    tool_node_module.ToolCallRequest = ToolCallRequest
    langgraph_types_module.Command = Command

    langchain_module.agents = agents_module
    langchain_module.messages = messages_module
    langchain_module.tools = tools_module
    agents_module.middleware = middleware_module
    tools_module.tool_node = tool_node_module
    langgraph_module.types = langgraph_types_module

    for name, module in [
        ("beav3r_sdk.client", beav3r_client_module),
        ("langchain", langchain_module),
        ("langchain.agents", agents_module),
        ("langchain.agents.middleware", middleware_module),
        ("langchain.messages", messages_module),
        ("langchain.tools", tools_module),
        ("langchain.tools.tool_node", tool_node_module),
        ("langgraph", langgraph_module),
        ("langgraph.types", langgraph_types_module),
    ]:
        sys.modules[name] = module
        created.append(name)

    return created


if __name__ == "__main__":
    unittest.main()
