from __future__ import annotations

import threading
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from storage.control_store import ControlStore
from tests.test_approval_revalidation import agent
from trading_agent.config import load_config
from trading_agent.control_state import AgentRuntimeState, ExecutionState, TradingMode
from trading_agent.service import (
    AUTO_DEMO_CONFIRMATION,
    KILL_SWITCH_RESET_CONFIRMATION,
    ServiceError,
    TradingService,
    sanitize_for_browser,
)
from trading_agent.web_api.app import create_app
from trading_agent.web_api.security import SessionManager


def ready_health(orchestrator):
    blockers = []
    if not orchestrator.state.execution_armed:
        blockers.append("EXECUTION_DISARMED")
    if orchestrator.state.runtime_mode.value == "STOPPED":
        blockers.append("TRADING_STOPPED")
    return {
        "system_capability": {"status": "READY"},
        "trading_eligibility": {
            "eligible": not blockers,
            "reason": blockers[0] if blockers else "ELIGIBLE",
            "blocking_reasons": blockers,
        },
        "live_trading": "LOCKED_NOT_IMPLEMENTED",
    }


def service_for(tmp_path, market, account) -> TradingService:
    orchestrator = agent(tmp_path, market, account)
    orchestrator.get_health = lambda: ready_health(orchestrator)
    service = TradingService(
        replace(load_config(), root=tmp_path), orchestrator,
        start_scheduler=False,
    )
    service.client_stream_connected("test-control-stream")
    return service


def test_startup_always_disarms_and_disables_auto_demo(tmp_path, market, account) -> None:
    store = ControlStore(tmp_path / "trading_agent.db")
    store.reset_for_startup()
    store.transition(
        "SIMULATE_PREVIOUS_PROCESS",
        execution_state=ExecutionState.ARMED,
        agent_runtime_state=AgentRuntimeState.RUNNING,
        trading_mode=TradingMode.AUTO,
        auto_demo_enabled=True,
    )
    store.close()

    service = service_for(tmp_path, market, account)
    try:
        state = service.control()
        assert state["execution_state"] == "DISARMED"
        assert state["agent_runtime_state"] == "STOPPED"
        assert state["trading_mode"] == "STOPPED"
        assert state["auto_demo_enabled"] is False
        assert service._orchestrator.state.execution_armed is False
    finally:
        service.close()


def test_mode_start_arm_disarm_and_kill_switch_are_fail_closed(tmp_path, market, account) -> None:
    service = service_for(tmp_path, market, account)
    try:
        service.set_mode("MANUAL_APPROVAL")
        service.start_agent()
        armed = service.arm()
        assert armed["execution_state"] == "ARMED"
        killed = service.activate_kill_switch()
        assert killed["kill_switch_active"] is True
        assert killed["execution_state"] == "DISARMED"
        assert killed["agent_runtime_state"] == "STOPPED"
        with pytest.raises(ServiceError, match="KILL_SWITCH_ACTIVE"):
            service.start_agent()
        reset = service.reset_kill_switch(KILL_SWITCH_RESET_CONFIRMATION)
        assert reset["kill_switch_active"] is False
        assert reset["execution_state"] == "DISARMED"
    finally:
        service.close()


def test_auto_demo_requires_exact_confirmation_and_is_session_only(tmp_path, market, account) -> None:
    service = service_for(tmp_path, market, account)
    try:
        with pytest.raises(ServiceError, match="EXACT_AUTO_DEMO_CONFIRMATION_REQUIRED"):
            service.enable_auto_demo("yes")
        assert service.enable_auto_demo(AUTO_DEMO_CONFIRMATION)["auto_demo_enabled"] is True
        assert service.set_mode("AUTO")["trading_mode"] == "AUTO"
        disabled = service.disable_auto_demo()
        assert disabled["auto_demo_enabled"] is False
        assert disabled["execution_state"] == "DISARMED"
        assert disabled["trading_mode"] == "MANUAL_APPROVAL"
    finally:
        service.close()


def test_local_api_requires_session_and_csrf_and_keeps_live_locked(tmp_path, market, account) -> None:
    service = service_for(tmp_path, market, account)
    app = create_app(service)
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/status").status_code == 401
            session_response = client.get("/api/v1/session")
            assert session_response.status_code == 200
            csrf = session_response.json()["csrf_token"]
            assert "httponly" in session_response.headers["set-cookie"].lower()
            status_response = client.get("/api/v1/status")
            assert status_response.status_code == 200
            assert "access-control-allow-origin" not in status_response.headers
            assert client.post("/api/v1/execution/disarm").status_code == 403
            headers = {"X-CSRF-Token": csrf}
            assert client.post("/api/v1/execution/disarm", headers=headers).status_code == 200
            assert client.post("/api/v1/execution/arm", headers=headers).status_code == 423
            live = client.post(
                "/api/v1/environment", headers=headers, json={"environment": "LIVE"},
            )
            assert live.status_code == 423
            assert live.json()["error"]["code"] == "LIVE_NOT_CONFIGURED"
            assert client.get("/api/v1/openapi.json").status_code == 200
            root = client.get("/")
            assert root.status_code == 200
            assert "text/html" in root.headers["content-type"] or root.json()["status"] == "FRONTEND_NOT_BUILT"
            assert "default-src 'self'" in root.headers["content-security-policy"]
            assert client.get("/api/v1/status", headers={"host": "attacker.invalid"}).status_code == 400
    finally:
        service.close()


def test_approval_endpoint_accepts_plan_id_not_client_order_fields(tmp_path, market, account) -> None:
    service = service_for(tmp_path, market, account)
    app = create_app(service)
    try:
        with TestClient(app) as client:
            client.get("/api/v1/session")
            schema = client.get("/api/v1/openapi.json").json()
            properties = schema["components"]["schemas"]["ApprovalRequest"]["properties"]
            assert set(properties) == {"confirmation"}
            required_paths = {
                "/api/v1/status", "/api/v1/health", "/api/v1/account",
                "/api/v1/scanner", "/api/v1/scanner/run", "/api/v1/analyze/{symbol}",
                "/api/v1/signals", "/api/v1/plans", "/api/v1/plans/pending",
                "/api/v1/orders", "/api/v1/positions", "/api/v1/trades",
                "/api/v1/logs", "/api/v1/settings", "/api/v1/backtest",
                "/api/v1/execution/arm", "/api/v1/execution/disarm",
                "/api/v1/agent/start", "/api/v1/agent/stop",
                "/api/v1/kill-switch",
                "/api/v1/plans/{plan_id}/approve",
                "/api/v1/plans/{plan_id}/reject",
            }
            assert required_paths.issubset(schema["paths"])
    finally:
        service.close()


def test_fresh_authenticated_websocket_unlocks_high_risk_route(tmp_path, market, account) -> None:
    service = service_for(tmp_path, market, account)
    app = create_app(service)
    try:
        with TestClient(app) as client:
            csrf = client.get("/api/v1/session").json()["csrf_token"]
            headers = {"X-CSRF-Token": csrf}
            assert client.post(
                "/api/v1/mode", headers=headers, json={"mode": "MANUAL_APPROVAL"},
            ).status_code == 200
            with client.websocket_connect(
                "/api/v1/ws", headers={"origin": "http://testserver"},
            ) as websocket:
                event = websocket.receive_json()
                assert event["type"] in {"snapshot", "snapshot.error"}
                assert client.post("/api/v1/agent/start", headers=headers).status_code == 200
                assert client.post("/api/v1/execution/arm", headers=headers).status_code == 200
            assert client.post("/api/v1/agent/start", headers=headers).status_code == 423
    finally:
        service.close()


def test_kill_switch_serializes_against_arm_and_always_wins(tmp_path, market, account) -> None:
    service = service_for(tmp_path, market, account)
    try:
        service.set_mode("MANUAL_APPROVAL")
        service.start_agent()
        service.refresh_health()
        barrier = threading.Barrier(2)

        def arm() -> None:
            barrier.wait()
            try:
                service.arm()
            except ServiceError:
                pass

        def kill() -> None:
            barrier.wait()
            service.activate_kill_switch()

        threads = [threading.Thread(target=arm), threading.Thread(target=kill)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        state = service.control()
        assert state["kill_switch_active"] is True
        assert state["execution_state"] == "DISARMED"
        with pytest.raises(PermissionError, match="KILL_SWITCH_ACTIVE"):
            service._final_entry_guard()
    finally:
        service.close()


def test_kill_switch_preserves_existing_protective_orders(tmp_path, market, account) -> None:
    service = service_for(tmp_path, market, account)
    try:
        plan = service._run_core(service._orchestrator.analyze, "BTC-USDT")
        service._run_core(
            service._orchestrator.trade_store.upsert_managed_position,
            plan.plan_id, "entry-1", plan.symbol, 0.01, plan.entry,
            "FILLED", "PROTECTED", ["sl-1", "tp-1"],
        )
        service.activate_kill_switch()
        position = service._run_core(service._orchestrator.trade_store.managed_positions)[0]
        assert position.protection_state == "PROTECTED"
        assert position.protective_order_ids_json == '["sl-1", "tp-1"]'
    finally:
        service.close()


def test_auto_runtime_uses_existing_plan_id_and_exact_core_approval(tmp_path, market, account) -> None:
    service = service_for(tmp_path, market, account)
    try:
        service.enable_auto_demo(AUTO_DEMO_CONFIRMATION)
        service.set_mode("AUTO")
        service.start_agent()
        service.arm()
        calls: list[tuple[str, str]] = []
        service.scan = lambda: {
            "BTC-USDT": {"decision": "BUY", "risk_approved": True, "plan_id": "server-plan-1"}
        }
        service.approve_plan = lambda plan_id, confirmation: (
            calls.append((plan_id, confirmation)) or {"status": "SUBMITTED"}
        )
        service.runtime_tick()
        assert calls == [("server-plan-1", "CONFIRM DEMO ORDER")]
        service.client_stream_disconnected("test-control-stream")
        service.runtime_tick()
        assert calls == [("server-plan-1", "CONFIRM DEMO ORDER")]
        with pytest.raises(PermissionError, match="CONTROL_STREAM_NOT_FRESH"):
            service._final_entry_guard()
    finally:
        service.close()


def test_browser_projection_redacts_sensitive_keys_and_bearer_values() -> None:
    sanitized = sanitize_for_browser({
        "api_key": "do-not-show",
        "nested": {"passphrase": "do-not-show", "message": "Authorization: Bearer abc.def"},
    })
    assert sanitized["api_key"] == "[REDACTED]"
    assert sanitized["nested"]["passphrase"] == "[REDACTED]"
    assert "abc.def" not in sanitized["nested"]["message"]


def test_runtime_failure_degrades_and_disarms(tmp_path, market, account) -> None:
    service = service_for(tmp_path, market, account)
    try:
        service.set_mode("MANUAL_APPROVAL")
        service.start_agent()
        service.arm()
        service.handle_runtime_failure(RuntimeError("simulated transport failure"))
        control = service.control()
        assert control["agent_runtime_state"] == "DEGRADED"
        assert control["execution_state"] == "DISARMED"
        assert control["connection_state"] == "STALE"
    finally:
        service.close()


def test_multiple_authenticated_websockets_do_not_clear_each_other() -> None:
    manager = SessionManager()
    session = manager.create()
    first = manager.register_websocket(session.cookie)
    second = manager.register_websocket(session.cookie)
    assert manager.websocket_fresh(session.cookie)
    manager.clear_websocket(session.cookie, first)
    assert manager.websocket_fresh(session.cookie)
    manager.clear_websocket(session.cookie, second)
    assert not manager.websocket_fresh(session.cookie)
