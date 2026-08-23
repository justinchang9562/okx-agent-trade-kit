from __future__ import annotations

import json

import pytest

from execution.base_backend import BackendStatus
from execution.demo_executor import DemoExecutor
from execution.errors import PreSubmitRejectedError
from execution.mcp_backend import MCPBackend, MCPError, MCPToolError, StdioMCPClient
from execution.order_manager import OrderManager
from execution.order_state import OrderState
from execution.targeted_reconciler import TargetedOrderReconciler
from storage.trade_store import TradeStore
from tests.test_core_hardening import plan_for


def _client_with_result(result: dict) -> StdioMCPClient:
    client = StdioMCPClient.__new__(StdioMCPClient)
    client.server_version = None
    client._request = lambda _method, _params: result
    return client


def _structured_error(**overrides) -> dict:
    payload = {
        "tool": "spot_place_order",
        "error": True,
        "type": "OkxApiError",
        "code": "401",
        "message": "example diagnostic",
        "suggestion": "verify Demo credentials",
        "endpoint": "POST /api/v5/trade/order",
        "traceId": "trace-test",
        "serverVersion": "1.4.4",
    }
    payload.update(overrides)
    return payload


def test_tools_call_structured_error_preserves_whitelisted_diagnostics() -> None:
    client = _client_with_result({"isError": True, "structuredContent": _structured_error()})

    with pytest.raises(MCPToolError) as raised:
        client.call_tool("spot_place_order", {})

    error = raised.value
    assert error.tool_name == "spot_place_order"
    assert error.error_type == "OkxApiError"
    assert error.code == "401"
    assert error.message == "example diagnostic"
    assert error.endpoint == "POST /api/v5/trade/order"
    assert error.trace_id == "trace-test"
    assert error.server_version == "1.4.4"


def test_tools_call_parses_json_text_error_when_structured_content_is_missing() -> None:
    client = _client_with_result({
        "isError": True,
        "content": [{"type": "text", "text": json.dumps(_structured_error())}],
    })

    with pytest.raises(MCPToolError) as raised:
        client.call_tool("spot_place_order", {})

    assert raised.value.code == "401"
    assert raised.value.endpoint == "POST /api/v5/trade/order"
    assert raised.value.trace_id == "trace-test"


@pytest.mark.parametrize("content", [[{"text": "not-json"}], None, {"text": "not-a-list"}])
def test_tools_call_unparseable_error_falls_back_without_crashing(content) -> None:
    client = _client_with_result({"isError": True, "content": content})

    with pytest.raises(MCPToolError) as raised:
        client.call_tool("spot_place_order", {})

    assert str(raised.value) == "MCP_TOOL_ERROR:spot_place_order"
    assert raised.value.safe_details == {"tool": "spot_place_order"}


def test_mcp_diagnostics_remove_or_redact_all_credential_shaped_data() -> None:
    secrets = [
        "test-key-value",
        "test-secret-value",
        "test-phrase-value",
        "test-auth-value",
        "test-sig-value",
        "test-token-value",
    ]
    payload = _structured_error(
        message=(
            "apiKey=test-key-value secretKey=test-secret-value passphrase=test-phrase-value "
            "authorization=test-auth-value signature=test-sig-value token=test-token-value"
        ),
        apiKey="test-top-level-key",
        secretKey="test-top-level-secret",
        passphrase="test-top-level-passphrase",
        authorization="test-top-level-authorization",
        signature="test-top-level-signature",
        token="test-top-level-token",
    )
    error = MCPToolError("spot_place_order", payload)
    serialized = json.dumps(error.safe_details, sort_keys=True)

    assert all(secret not in serialized for secret in secrets)
    assert "test-top-level-" not in serialized
    assert "[REDACTED]" in serialized
    assert set(error.safe_details) == {
        "tool", "type", "code", "message", "suggestion", "endpoint", "trace_id", "server_version",
    }
    wrapped = PreSubmitRejectedError(
        "EXCHANGE_EXPLICIT_REJECTION",
        diagnostics=payload,
        display_message="signature=test-display-signature",
    )
    assert "test-display-signature" not in str(wrapped)
    assert "test-top-level-" not in json.dumps(wrapped.diagnostics, sort_keys=True)


class FakeMCPClient:
    def __init__(self, error: MCPError) -> None:
        self.error = error
        self.place_calls = 0

    def call_tool(self, name: str, _arguments: dict) -> dict:
        assert name == "spot_place_order"
        self.place_calls += 1
        raise self.error


class HarnessMCPBackend(MCPBackend):
    def status(self) -> BackendStatus:
        return BackendStatus("mcp", "CONNECTED", True, True)

    def capabilities(self) -> dict:
        return {"attached_tp_sl": True, "client_order_id": True}

    def get_order_by_client_id(self, symbol: str, client_order_id: str) -> dict:
        return {"data": {"data": []}}


def _manager(tmp_path, long_signal, error: MCPError):
    client = FakeMCPClient(error)
    backend = HarnessMCPBackend(client=client)
    plan = plan_for(long_signal)
    store = TradeStore(tmp_path / "observability.db")
    store.save_plan(plan)
    manager = OrderManager(
        DemoExecutor(backend),
        store,
        TargetedOrderReconciler((0.0,), lambda _delay: None),
    )
    return client, plan, store, manager


def test_explicit_mcp_rejection_stays_rejected_with_diagnostics_and_no_retry(
    tmp_path, long_signal,
) -> None:
    client, plan, store, manager = _manager(
        tmp_path,
        long_signal,
        MCPToolError("spot_place_order", _structured_error()),
    )

    with pytest.raises(PreSubmitRejectedError, match="PRE_SUBMIT_REJECTED") as raised:
        manager.submit(plan, "CONFIRM DEMO ORDER")

    assert raised.value.reason == "EXCHANGE_EXPLICIT_REJECTION"
    assert raised.value.diagnostics["code"] == "401"
    assert raised.value.diagnostics["endpoint"] == "POST /api/v5/trade/order"
    assert raised.value.diagnostics["trace_id"] == "trace-test"
    assert store.order_for_plan(plan.plan_id)["state"] == OrderState.REJECTED.value
    assert client.place_calls == 1
    store.close()


def test_timeout_stays_submission_unknown_and_never_submits_twice(tmp_path, long_signal) -> None:
    client, plan, store, manager = _manager(tmp_path, long_signal, MCPError("MCP_TIMEOUT"))

    with pytest.raises(RuntimeError, match="SUBMISSION_UNKNOWN"):
        manager.submit(plan, "CONFIRM DEMO ORDER")

    assert store.order_for_plan(plan.plan_id)["state"] == OrderState.SUBMISSION_UNKNOWN.value
    assert client.place_calls == 1
    store.close()
