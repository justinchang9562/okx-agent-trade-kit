from __future__ import annotations

import threading
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from data.realtime.models import ConfirmedCandleEvent
from execution.order_state import OrderState
from execution.targeted_reconciler import TargetedOrderReconciler
from storage.control_store import ControlStore
from storage.trade_store import now_ms
from tests.test_approval_revalidation import agent
from tests.test_core_hardening import plan_for
from tests.test_order_lifecycle_v041 import RaceBackend, RaceExecutor, row, valid_protection
from trading_agent.config import load_config
from trading_agent.control_state import AgentRuntimeState, ExecutionState, SessionState, TradingMode
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
    service.client_stream_acknowledged("test-control-stream")
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
        scan_interval_seconds=45,
    )
    store.close()

    service = service_for(tmp_path, market, account)
    try:
        state = service.control()
        assert state["execution_state"] == "DISARMED"
        assert state["session_state"] == "STOPPED"
        assert state["agent_runtime_state"] == "STOPPED"
        assert state["trading_mode"] == "STOPPED"
        assert state["auto_demo_enabled"] is False
        assert state["scan_interval_seconds"] == 45
        assert service._orchestrator.state.execution_armed is False
    finally:
        service.close()


def test_active_kill_switch_persists_across_restart(tmp_path, market, account) -> None:
    store = ControlStore(tmp_path / "trading_agent.db")
    store.reset_for_startup()
    store.transition("PREVIOUS_KILL", kill_switch_active=True, scan_interval_seconds=30)
    store.close()

    service = service_for(tmp_path, market, account)
    try:
        state = service.control()
        assert state["kill_switch_active"] is True
        assert state["execution_state"] == "DISARMED"
        assert state["agent_runtime_state"] == "STOPPED"
        assert state["scan_interval_seconds"] == 30
        with pytest.raises(ServiceError, match="KILL_SWITCH_ACTIVE"):
            service.start_agent()
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
            assert set(properties) == {"approval_challenge"}
            required_paths = {
                "/api/v1/status", "/api/v1/health", "/api/v1/account",
                "/api/v1/session/status", "/api/v1/session/start",
                "/api/v1/session/pause", "/api/v1/session/stop", "/api/v1/session/flatten",
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


def test_duplicate_browser_approval_consumes_challenge_once(tmp_path, market, account) -> None:
    service = service_for(tmp_path, market, account)
    app = create_app(service)
    calls: list[tuple[str, dict]] = []

    def approve_once(plan_id: str, preview: dict) -> dict:
        calls.append((plan_id, preview))
        return {"plan_id": plan_id, "status": "SUBMITTED"}

    service.approve_plan_with_challenge = approve_once  # type: ignore[method-assign]
    try:
        with TestClient(app) as client:
            csrf = client.get("/api/v1/session").json()["csrf_token"]
            cookie = client.cookies.get("okx_dashboard_session")
            challenge = app.state.sessions.create_approval_challenge(
                cookie,
                "plan-1",
                {"status": "READY_FOR_EXACT_APPROVAL", "position_size": .01},
            )
            body = {"approval_challenge": challenge["approval_challenge"]}
            headers = {"X-CSRF-Token": csrf}
            with client.websocket_connect(
                "/api/v1/ws", headers={"origin": "http://testserver"},
            ) as websocket:
                event = websocket.receive_json()
                websocket.send_json({"type": "heartbeat.ack", "sequence": event["sequence"]})
                websocket.receive_json()
                assert client.post(
                    "/api/v1/plans/plan-1/approve", headers=headers, json=body,
                ).status_code == 200
                duplicate = client.post(
                    "/api/v1/plans/plan-1/approve", headers=headers, json=body,
                )
                assert duplicate.status_code == 409
                assert duplicate.json()["error"]["code"] == "APPROVAL_CHALLENGE_INVALID_OR_USED"
            assert len(calls) == 1
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
                assert client.post("/api/v1/agent/start", headers=headers).status_code == 423
                websocket.send_json({"type": "heartbeat.ack", "sequence": event["sequence"]})
                websocket.receive_json()
                assert client.post("/api/v1/agent/start", headers=headers).status_code == 200
                assert client.post("/api/v1/execution/arm", headers=headers).status_code == 200
            assert client.post("/api/v1/agent/start", headers=headers).status_code == 423
    finally:
        service.close()


def test_malformed_websocket_message_closes_without_control_action(tmp_path, market, account) -> None:
    service = service_for(tmp_path, market, account)
    app = create_app(service)
    try:
        with TestClient(app) as client:
            client.get("/api/v1/session")
            with client.websocket_connect(
                "/api/v1/ws", headers={"origin": "http://testserver"},
            ) as websocket:
                websocket.receive_json()
                websocket.send_json({"type": "arm", "sequence": 0})
                with pytest.raises(WebSocketDisconnect) as closed:
                    websocket.receive_json()
                assert closed.value.code == 4400
            assert service.control()["execution_state"] == "DISARMED"
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


def test_auto_session_uses_existing_plan_id_without_per_trade_user_approval(
    tmp_path, market, account,
) -> None:
    service = service_for(tmp_path, market, account)
    try:
        service._session_preflight = lambda: []
        service.start_session()
        calls: list[str] = []
        service.scan = lambda: {
            "BTC-USDT": {"decision": "BUY", "risk_approved": True, "plan_id": "server-plan-1"}
        }
        service._orchestrator.execute_plan_automatically = lambda plan_id: (
            calls.append(plan_id) or {"status": "SUBMITTED"}
        )
        service._synchronize_account = lambda: {}
        service.runtime_tick()
        assert calls == ["server-plan-1"]
        service.client_stream_disconnected("test-control-stream")
        service.runtime_tick()
        assert calls == ["server-plan-1", "server-plan-1"]
        with pytest.raises(PermissionError, match="REALTIME_MARKET_NOT_CONFIGURED"):
            service._final_entry_guard()
    finally:
        service.close()


def test_session_start_pause_stop_are_atomic_and_preserve_managed_protection(
    tmp_path, market, account,
) -> None:
    service = service_for(tmp_path, market, account)
    try:
        service._session_preflight = lambda: []
        started = service.start_session()
        assert started["session_state"] == SessionState.RUNNING.value
        control = service.control()
        assert control["execution_state"] == "ARMED"
        assert control["trading_mode"] == "AUTO"
        assert control["auto_demo_enabled"] is True

        plan = service._run_core(service._orchestrator.analyze, "BTC-USDT")
        service._run_core(
            service._orchestrator.trade_store.upsert_managed_position,
            plan.plan_id, "entry-1", plan.symbol, 0.01, plan.entry,
            "FILLED", "PROTECTED", ["sl-1", "tp-1"],
        )
        paused = service.pause_session()
        assert paused["session_state"] == SessionState.PAUSED.value
        position = service._run_core(service._orchestrator.trade_store.managed_positions)[0]
        assert position.protection_state == "PROTECTED"
        assert position.protective_order_ids_json == '["sl-1", "tp-1"]'

        service._session_preflight = lambda: []
        assert service.start_session()["session_state"] == SessionState.RUNNING.value
        stopped = service.stop_session()
        assert stopped["session_state"] == SessionState.STOPPED.value
        position = service._run_core(service._orchestrator.trade_store.managed_positions)[0]
        assert position.protection_state == "PROTECTED"
    finally:
        service.close()


def test_flatten_does_not_report_flat_while_agent_entry_is_unresolved(
    tmp_path, market, account, long_signal,
) -> None:
    service = service_for(tmp_path, market, account)
    try:
        plan = plan_for(long_signal)
        service._run_core(service._orchestrator.trade_store.save_plan, plan)
        service._run_core(
            service._orchestrator.trade_store.approve_and_create_order,
            plan, plan.plan_id, plan.entry,
        )
        service._run_core(
            service._orchestrator.trade_store.transition_order,
            plan.plan_id, OrderState.SUBMITTED.value,
        )
        service._run_core(
            service._orchestrator.trade_store.transition_order,
            plan.plan_id, OrderState.SUBMISSION_UNKNOWN.value,
        )

        first = service.flatten_session()
        second = service._session.continue_flatten()

        assert first["status"] == "FLATTEN_INCOMPLETE"
        assert second["status"] == "FLATTEN_INCOMPLETE"
        assert service.control()["session_state"] == SessionState.FLATTENING.value
    finally:
        service.close()


@pytest.mark.parametrize("blocker", [
    "OKX_DEMO_BACKEND_UNAVAILABLE",
    "REALTIME_MARKET_NOT_READY",
    "KILL_SWITCH_ACTIVE",
    "POSITION_UNPROTECTED",
])
def test_session_start_fails_closed_for_preflight_blockers(tmp_path, market, account, blocker) -> None:
    service = service_for(tmp_path, market, account)
    try:
        service._session_preflight = lambda: [blocker]
        with pytest.raises(ServiceError, match=blocker):
            service.start_session()
        control = service.control()
        assert control["session_state"] == "STOPPED"
        assert control["execution_state"] == "DISARMED"
        assert control["auto_demo_enabled"] is False
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


def test_stale_strategy_event_is_dropped_before_analysis(tmp_path, market, account) -> None:
    service = service_for(tmp_path, market, account)
    try:
        service._session_preflight = lambda: []
        service.start_session()
        calls: list[str] = []
        service._orchestrator.analyze = lambda symbol: calls.append(symbol)
        service._handle_confirmed_candle(ConfirmedCandleEvent(
            "BTC-USDT", "1m", now_ms() - 60_000, now_ms() - 10_000,
        ))
        assert calls == []
        assert any(event["reason"] == "STALE_STRATEGY_EVENT" for event in service.events_after(0))
    finally:
        service.close()


def test_market_event_queue_dedupes_and_fails_closed_on_backpressure(
    tmp_path, market, account,
) -> None:
    config = replace(load_config(), root=tmp_path)
    config.rules["realtime"]["event_queue_capacity"] = 1
    orchestrator = agent(tmp_path, market, account)
    orchestrator.get_health = lambda: ready_health(orchestrator)
    service = TradingService(config, orchestrator, start_scheduler=False)
    service._market_event_stop.set()
    service._event_worker.join(timeout=2)
    service.client_stream_connected("test-control-stream")
    service.client_stream_acknowledged("test-control-stream")
    try:
        service._session_preflight = lambda: []
        service.start_session()
        first = ConfirmedCandleEvent("BTC-USDT", "1m", 1, now_ms())
        service._on_confirmed_candle(first)
        service._on_confirmed_candle(first)
        assert service._market_event_queue.qsize() == 1
        service._on_confirmed_candle(ConfirmedCandleEvent("ETH-USDT", "1m", 2, now_ms()))
        assert service.control()["session_state"] == "DEGRADED"
        assert any(
            event["reason"] == "MARKET_EVENT_BACKLOG_OVERFLOW"
            for event in service.events_after(0)
        )
    finally:
        service.close()


@pytest.mark.parametrize(
    ("action", "expected_session"),
    [("pause_session", "PAUSED"), ("stop_session", "STOPPED")],
)
def test_pause_or_stop_survives_cancel_fill_race(
    tmp_path, market, account, long_signal, action, expected_session,
) -> None:
    service = service_for(tmp_path, market, account)
    try:
        service._session_preflight = lambda: []
        service.start_session()
        plan = plan_for(long_signal)
        store = service._orchestrator.trade_store
        service._run_core(store.save_plan, plan)
        service._run_core(store.approve_and_create_order, plan, plan.plan_id, plan.entry)
        service._run_core(
            store.transition_order, plan.plan_id, OrderState.SUBMITTED.value,
            okx_order_id="entry-1",
        )
        service._run_core(store.transition_order, plan.plan_id, OrderState.OPEN.value)
        backend = RaceBackend()
        backend.lookups.append(row(plan, "filled", plan.position_size))
        backend.protections.append(valid_protection(plan))
        service._orchestrator.order_manager.executor = RaceExecutor(backend)
        service._orchestrator.order_manager.targeted_reconciler = TargetedOrderReconciler(
            (0.0, 0.0), lambda _delay: None,
        )

        result = getattr(service, action)()

        assert result["session_state"] == expected_session
        position = service._run_core(store.managed_positions)[0]
        assert position.quantity == plan.position_size
        assert position.protection_state == "PROTECTED"
        assert backend.cancel_calls == 1
    finally:
        service.close()


def test_multiple_authenticated_websockets_do_not_clear_each_other() -> None:
    manager = SessionManager()
    session = manager.create()
    first = manager.register_websocket(session.cookie)
    second = manager.register_websocket(session.cookie)
    assert not manager.websocket_fresh(session.cookie)
    assert manager.acknowledge_websocket(session.cookie, first, 0)
    assert manager.websocket_fresh(session.cookie)
    manager.clear_websocket(session.cookie, first)
    assert not manager.websocket_fresh(session.cookie)
    assert manager.acknowledge_websocket(session.cookie, second, 0)
    assert manager.websocket_fresh(session.cookie)
    manager.clear_websocket(session.cookie, second)
    assert not manager.websocket_fresh(session.cookie)


def test_client_ack_becomes_stale_after_eight_seconds(monkeypatch) -> None:
    import trading_agent.web_api.security as security

    clock = [100.0]
    monkeypatch.setattr(security.time, "monotonic", lambda: clock[0])
    manager = SessionManager()
    session = manager.create()
    connection = manager.register_websocket(session.cookie)
    assert manager.acknowledge_websocket(session.cookie, connection, 0)
    clock[0] = 108.0
    assert manager.websocket_fresh(session.cookie)
    clock[0] = 108.001
    assert not manager.websocket_fresh(session.cookie)
