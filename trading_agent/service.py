from __future__ import annotations

import queue
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from typing import Any, TypeVar

from data.realtime.instrument_cache import InstrumentCache
from data.realtime.models import ConfirmedCandleEvent
from data.realtime.runtime import RealtimeMarketRuntime
from storage.control_store import ControlStore
from storage.trade_store import now_ms
from trading_agent.account_synchronizer import AccountSynchronizer
from trading_agent.backtest_service import BacktestService
from trading_agent.config import AppConfig
from trading_agent.control_api import LocalControlAPI
from trading_agent.control_state import (
    AgentRuntimeState,
    ConnectionState,
    EnvironmentState,
    ExecutionState,
    SessionState,
    TradingMode,
)
from trading_agent.orchestrator import TradingOrchestrator
from trading_agent.session_controller import AutoTradingSessionController, SessionPreflightError
from trading_agent.state import RuntimeMode

T = TypeVar("T")
AUTO_DEMO_CONFIRMATION = "ENABLE AUTO DEMO"
APPROVAL_CONFIRMATION = "CONFIRM DEMO ORDER"
KILL_SWITCH_RESET_CONFIRMATION = "RESET KILL SWITCH"
_REDACT = re.compile(
    r"(?i)(api[_-]?key|secret|passphrase|authorization|bearer|token)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
_AUTHORIZATION = re.compile(r"(?i)(authorization\s*[:=]\s*)[^\r\n]+")
_SENSITIVE_KEY = re.compile(r"(?i)(api.?key|secret|passphrase|authorization|credential|access.?token)")


def sanitize_for_browser(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _SENSITIVE_KEY.search(str(key)) else sanitize_for_browser(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_for_browser(item) for item in value]
    if isinstance(value, str):
        redacted = _AUTHORIZATION.sub(r"\1[REDACTED]", value)
        redacted = _BEARER.sub(r"\1[REDACTED]", redacted)
        return _REDACT.sub(r"\1\2[REDACTED]", redacted)
    return value


def serialized_action(function):
    @wraps(function)
    def locked(self, *args, **kwargs):
        with self._action_lock:
            return function(self, *args, **kwargs)
    return locked


class ServiceError(RuntimeError):
    def __init__(self, code: str, status_code: int = 409, detail: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.detail = detail or code


class TradingService:
    """Single authoritative owner for Core, runtime controls and UI projections.

    Every call into ``TradingOrchestrator`` runs on one worker.  The Web layer
    never holds an adapter, executor or credential-bearing object.
    """

    def __init__(
        self,
        config: AppConfig,
        orchestrator: TradingOrchestrator | None = None,
        *,
        start_scheduler: bool = True,
        backtest_service: BacktestService | None = None,
        start_realtime: bool | None = None,
        realtime_runtime: RealtimeMarketRuntime | None = None,
    ) -> None:
        self.config = config
        self._orchestrator = orchestrator or TradingOrchestrator(config)
        self._api = LocalControlAPI(self._orchestrator)
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="trading-core")
        queue_capacity = int(config.rules.get("realtime", {}).get("event_queue_capacity", 16))
        self._market_event_queue: queue.Queue[ConfirmedCandleEvent] = queue.Queue(
            maxsize=max(1, queue_capacity),
        )
        self._market_event_stop = threading.Event()
        self._market_event_lock = threading.RLock()
        self._market_event_keys: deque[tuple[str, str, int]] = deque(maxlen=256)
        self._market_event_key_set: set[tuple[str, str, int]] = set()
        self._event_worker = threading.Thread(
            target=self._market_event_loop, name="market-events", daemon=True,
        )
        self._event_worker.start()
        self._backtests = backtest_service or BacktestService(config)
        self._account_sync = AccountSynchronizer(float(
            config.rules.get("runtime", {}).get("account_sync_interval_seconds", 3),
        ))
        self._control = ControlStore(config.root / "trading_agent.db")
        self._events: deque[dict[str, Any]] = deque(maxlen=500)
        self._event_lock = threading.RLock()
        self._action_lock = threading.RLock()
        self._client_lock = threading.RLock()
        self._client_streams: dict[str, float] = {}
        self._scheduler_stop = threading.Event()
        self._scheduler: threading.Thread | None = None
        self._closed = False
        self._last_scan: dict[str, Any] = {}
        self._last_health: dict[str, Any] = {}
        self._last_account: dict[str, Any] = {}
        self._last_status: dict[str, Any] = {}
        self._last_orders: dict[str, Any] = {}
        self._last_positions: dict[str, Any] = {}
        self._last_fills: list[dict[str, Any]] = []
        self._last_market: dict[str, Any] = {}
        self._last_scan_at_ms: int | None = None
        self._last_health_at_ms: int | None = None
        self._last_account_at_ms: int | None = None
        self._realtime: RealtimeMarketRuntime | None = realtime_runtime
        self._orchestrator.order_manager.executor.entry_guard = self._final_entry_guard
        self._session = AutoTradingSessionController(self)
        self._run_core(self._safe_startup)
        realtime_enabled = bool(config.rules.get("realtime", {}).get("enabled", True))
        should_start_realtime = start_scheduler if start_realtime is None else start_realtime
        if self._realtime is None and should_start_realtime and realtime_enabled:
            self._realtime = RealtimeMarketRuntime(
                self._orchestrator.adapter.backend,
                config.symbols,
                config.rules,
                InstrumentCache(self._orchestrator.adapter.backend),
                self._on_confirmed_candle,
            )
        if self._realtime is not None:
            self._run_core(self._orchestrator.set_market_provider, self._realtime.provider)
            if should_start_realtime:
                self._realtime.start()
        if start_scheduler:
            self._scheduler = threading.Thread(
                target=self._scheduler_loop, name="trading-runtime", daemon=True,
            )
            self._scheduler.start()

    def _run_core(self, function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        if self._closed:
            raise ServiceError("SERVICE_CLOSED", 503)
        return self._worker.submit(function, *args, **kwargs).result()

    def _safe_startup(self) -> None:
        self._orchestrator.state.disarm_execution()
        self._orchestrator.state.auto_demo_enabled = False
        self._orchestrator.state.set_mode(RuntimeMode.STOPPED)
        snapshot = self._control.reset_for_startup()
        self._orchestrator.state.kill_switch_active = snapshot.kill_switch_active
        self._emit("control.state", snapshot.as_dict(), "SAFE_RESTART")
        try:
            recovery = self._api.agent.recover()
            self._emit("orders.reconciled", {"results": recovery})
        except Exception as exc:
            self._emit("orders.recovery_failed", {"error": type(exc).__name__})

    def _emit(self, event_type: str, payload: dict[str, Any], reason: str = "PASS") -> None:
        with self._event_lock:
            sequence = self._events[-1]["sequence"] + 1 if self._events else 1
            self._events.append({
                "schema_version": "1.0",
                "sequence": sequence,
                "timestamp_ms": now_ms(),
                "type": event_type,
                "reason": reason,
                "data": sanitize_for_browser(payload),
            })

    def events_after(self, sequence: int) -> list[dict[str, Any]]:
        with self._event_lock:
            return [dict(item) for item in self._events if int(item["sequence"]) > sequence]

    @property
    def latest_sequence(self) -> int:
        with self._event_lock:
            return int(self._events[-1]["sequence"]) if self._events else 0

    def control(self) -> dict[str, Any]:
        return self._control.get().as_dict()

    def client_stream_connected(self, connection_id: str) -> None:
        with self._client_lock:
            self._client_streams[connection_id] = 0.0

    def client_stream_acknowledged(self, connection_id: str) -> None:
        with self._client_lock:
            if connection_id in self._client_streams:
                self._client_streams[connection_id] = time.monotonic()

    def client_stream_disconnected(self, connection_id: str) -> None:
        with self._client_lock:
            self._client_streams.pop(connection_id, None)

    def client_stream_fresh(self, max_age_seconds: float = 8.0) -> bool:
        with self._client_lock:
            heartbeats = tuple(self._client_streams.values())
        acknowledgements = [timestamp for timestamp in heartbeats if timestamp > 0]
        return bool(acknowledgements) and time.monotonic() - max(acknowledgements) <= max_age_seconds

    def client_stream_freshness_age_seconds(self) -> float | None:
        with self._client_lock:
            acknowledgements = [timestamp for timestamp in self._client_streams.values() if timestamp > 0]
        return time.monotonic() - max(acknowledgements) if acknowledgements else None

    def _sync_core_state(self) -> None:
        control = self._control.get()
        mode = {
            TradingMode.STOPPED: RuntimeMode.STOPPED,
            TradingMode.DRY_RUN: RuntimeMode.DRY_RUN,
            TradingMode.MANUAL_APPROVAL: RuntimeMode.MANUAL_APPROVAL,
            TradingMode.AUTO: RuntimeMode.AUTO_DEMO,
        }[control.trading_mode]
        self._orchestrator.state.auto_demo_enabled = control.auto_demo_enabled
        self._orchestrator.state.kill_switch_active = control.kill_switch_active
        if control.agent_runtime_state is AgentRuntimeState.STOPPED:
            self._orchestrator.state.set_mode(RuntimeMode.STOPPED)
        else:
            self._orchestrator.state.set_mode(mode)
        if control.execution_state is ExecutionState.ARMED:
            self._orchestrator.state.arm_execution()
        else:
            self._orchestrator.state.disarm_execution()

    def _final_entry_guard(self) -> None:
        """Last-moment gate used by DemoExecutor immediately before submission."""
        control = self._control.get()
        if control.kill_switch_active:
            raise PermissionError("KILL_SWITCH_ACTIVE")
        if control.environment is not EnvironmentState.DEMO:
            raise PermissionError("LIVE_NOT_CONFIGURED")
        if control.connection_state is not ConnectionState.CONNECTED:
            raise PermissionError("BACKEND_CONNECTION_NOT_FRESH")
        if control.agent_runtime_state is not AgentRuntimeState.RUNNING:
            raise PermissionError("AGENT_NOT_RUNNING")
        if control.execution_state is not ExecutionState.ARMED:
            raise PermissionError("EXECUTION_DISARMED")
        if control.session_state is not SessionState.RUNNING:
            raise PermissionError("AUTO_SESSION_NOT_RUNNING")
        if self._realtime is None:
            raise PermissionError("REALTIME_MARKET_NOT_CONFIGURED")
        self._realtime.state.assert_entry_ready()
        self._orchestrator.state.require_new_entry_allowed()

    def _transition(self, action: str, reason: str = "PASS", **changes: Any) -> dict[str, Any]:
        with self._action_lock:
            snapshot = self._control.transition(action, reason=reason, **changes)
            try:
                self._run_core(self._sync_core_state)
            except Exception as exc:
                safe = self._control.transition(
                    "CONTROL_SYNC_FAILURE_SAFE_RESET", actor="system", reason=type(exc).__name__,
                    execution_state=ExecutionState.DISARMED,
                    agent_runtime_state=AgentRuntimeState.STOPPED,
                    trading_mode=TradingMode.STOPPED,
                    auto_demo_enabled=False,
                    connection_state=ConnectionState.STALE,
                )
                self._run_core(self._force_core_safe)
                self._emit("control.state", safe.as_dict(), "CONTROL_SYNC_FAILURE_SAFE_RESET")
                raise ServiceError("CONTROL_TRANSITION_FAILED_SAFE", 503) from exc
            result = snapshot.as_dict()
            self._emit("control.state", result, reason)
            return result

    def _force_core_safe(self) -> None:
        self._orchestrator.state.disarm_execution()
        self._orchestrator.state.auto_demo_enabled = False
        self._orchestrator.state.runtime_mode = RuntimeMode.STOPPED

    def _session_preflight(self) -> list[str]:
        blockers: list[str] = []
        control = self._control.get()
        if control.environment is not EnvironmentState.DEMO or self.config.environment != "demo":
            blockers.append("DEMO_ENVIRONMENT_REQUIRED")
        live = self.config.environments.get("environments", {}).get("live", {})
        if bool(live.get("enabled", False)):
            blockers.append("LIVE_MUST_REMAIN_LOCKED")
        if control.kill_switch_active:
            blockers.append("KILL_SWITCH_ACTIVE")
        if not self.client_stream_fresh():
            blockers.append("CONTROL_STREAM_NOT_FRESH")
        try:
            backend_status = self._run_core(self._orchestrator.adapter.backend.status)
            if not backend_status.available or not backend_status.demo:
                blockers.append("OKX_DEMO_BACKEND_UNAVAILABLE")
            capabilities = self._run_core(self._orchestrator.adapter.backend.capabilities)
            if not capabilities.get("attached_tp_sl", False):
                blockers.append("TP_SL_BACKEND_NOT_SUPPORTED")
        except Exception:
            blockers.append("OKX_DEMO_BACKEND_UNAVAILABLE")
        if self._realtime is None:
            blockers.append("REALTIME_MARKET_NOT_CONFIGURED")
        else:
            try:
                self._realtime.state.assert_entry_ready()
                self._last_market = sanitize_for_browser(self._realtime.status())
            except Exception as exc:
                blockers.append(str(exc) if str(exc).startswith("MARKET_") else "REALTIME_MARKET_NOT_READY")
        try:
            recovery = self._run_core(self._orchestrator.recover)
            self._emit("orders.reconciled", {"results": recovery})
        except Exception:
            blockers.append("RECONCILIATION_UNAVAILABLE")
        try:
            self._synchronize_account()
        except Exception:
            blockers.append("ACCOUNT_DATA_UNAVAILABLE")
        stale_after = float(self.config.rules.get("runtime", {}).get("account_stale_after_seconds", 10))
        if self._last_account_at_ms is None or now_ms() - self._last_account_at_ms > stale_after * 1000:
            blockers.append("ACCOUNT_STATE_STALE")
        try:
            health = self.refresh_health()
            health_blockers = set(health.get("trading_eligibility", {}).get("blocking_reasons", []))
            health_blockers.difference_update({"EXECUTION_DISARMED", "TRADING_STOPPED"})
            blockers.extend(sorted(health_blockers))
            if health.get("system_capability", {}).get("status") != "READY":
                blockers.append("SYSTEM_CAPABILITY_NOT_READY")
        except Exception:
            blockers.append("RISK_SYSTEM_UNAVAILABLE")
        try:
            orders = self._run_core(self._orchestrator.trade_store.get_orders)
            if any(item.get("state") == "SUBMISSION_UNKNOWN" for item in orders):
                blockers.append("SUBMISSION_UNKNOWN_REQUIRES_RECONCILIATION")
            if any(item.get("state") == "CANCEL_REQUESTED" for item in orders):
                blockers.append("CANCEL_REQUEST_REQUIRES_RECONCILIATION")
            positions = self._run_core(self._orchestrator.trade_store.managed_positions)
            if any(item.protection_state != "PROTECTED" for item in positions):
                blockers.append("POSITION_UNPROTECTED")
        except Exception:
            blockers.append("RECONCILIATION_STATE_UNAVAILABLE")
        if self._orchestrator.risk is None:
            blockers.append("RISK_SYSTEM_UNAVAILABLE")
        if self._orchestrator.strategy is None:
            blockers.append("STRATEGY_NOT_READY")
        return list(dict.fromkeys(blockers))

    @serialized_action
    def start_session(self) -> dict[str, Any]:
        try:
            result = self._session.start()
        except SessionPreflightError as exc:
            self._emit("session.start_rejected", {"blockers": exc.blockers}, exc.blockers[0])
            raise ServiceError(exc.blockers[0], 423, ", ".join(exc.blockers)) from exc
        self._emit("session.started", result, "PASS")
        return sanitize_for_browser(result)

    @serialized_action
    def pause_session(self) -> dict[str, Any]:
        result = self._session.pause()
        self._emit("session.paused", result, "NEW_ENTRIES_BLOCKED_PROTECTION_PRESERVED")
        return sanitize_for_browser(result)

    @serialized_action
    def stop_session(self) -> dict[str, Any]:
        result = self._session.stop()
        self._emit("session.stopped", result, "SESSION_STOPPED_PROTECTION_PRESERVED")
        return sanitize_for_browser(result)

    @serialized_action
    def flatten_session(self) -> dict[str, Any]:
        result = self._session.flatten()
        self._emit("session.flatten", result, str(result["status"]))
        return sanitize_for_browser(result)

    def session_status(self) -> dict[str, Any]:
        status = self._session.status()
        if status["session_state"] == SessionState.DEGRADED.value:
            degraded = next(
                (
                    item for item in self._control.list_audit(20)
                    if item.get("requested_action") == "SESSION_FAIL_CLOSED"
                ),
                None,
            )
            status["degraded_reason"] = degraded.get("reason") if degraded else None
        status["market"] = self._last_market
        status["account_freshness_age_seconds"] = (
            (now_ms() - self._last_account_at_ms) / 1000 if self._last_account_at_ms else None
        )
        return sanitize_for_browser(status)

    def _on_confirmed_candle(self, event: ConfirmedCandleEvent) -> None:
        if self._closed:
            return
        key = (event.symbol, event.timeframe, event.candle_timestamp_ms)
        with self._market_event_lock:
            if key in self._market_event_key_set:
                return
            if len(self._market_event_keys) == self._market_event_keys.maxlen:
                self._market_event_key_set.discard(self._market_event_keys[0])
            self._market_event_keys.append(key)
            self._market_event_key_set.add(key)
        try:
            self._market_event_queue.put_nowait(event)
        except queue.Full:
            with self._market_event_lock:
                self._market_event_key_set.discard(key)
                if self._market_event_keys and self._market_event_keys[-1] == key:
                    self._market_event_keys.pop()
            if self._realtime is not None:
                self._realtime.state.metrics.increment("critical_queue_overflows")
            self._session.degrade("MARKET_EVENT_BACKLOG_OVERFLOW")
            self._emit(
                "strategy.event_dropped",
                {"symbol": event.symbol, "candle_timestamp_ms": event.candle_timestamp_ms},
                "MARKET_EVENT_BACKLOG_OVERFLOW",
            )
            return

    def _market_event_loop(self) -> None:
        while not self._market_event_stop.is_set():
            try:
                event = self._market_event_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                self._handle_confirmed_candle(event)
            finally:
                self._market_event_queue.task_done()

    def _handle_confirmed_candle(self, event: ConfirmedCandleEvent) -> None:
        control = self._control.get()
        if event.timeframe != "1m" or control.session_state is not SessionState.RUNNING:
            return
        max_age_ms = int(
            float(self.config.rules.get("realtime", {}).get("strategy_event_max_age_seconds", 5))
            * 1000
        )
        if now_ms() - event.received_timestamp_ms > max_age_ms:
            if self._realtime is not None:
                self._realtime.state.metrics.increment("stale_strategy_events")
            self._emit(
                "strategy.event_dropped",
                {"symbol": event.symbol, "candle_timestamp_ms": event.candle_timestamp_ms},
                "STALE_STRATEGY_EVENT",
            )
            return
        started = now_ms()
        created = False
        try:
            if self._realtime is None:
                raise PermissionError("REALTIME_MARKET_NOT_CONFIGURED")
            self._realtime.state.assert_entry_ready(event.symbol)
            plan = self._run_core(self._orchestrator.analyze, event.symbol)
            payload = sanitize_for_browser(plan.as_dict())
            self._last_scan[event.symbol] = payload
            self._last_scan_at_ms = now_ms()
            self._emit("scanner.updated", {event.symbol: payload})
            created = bool(plan.plan_id)
            if plan.decision == "BUY" and plan.risk_approved:
                outcome = self._run_core(self._orchestrator.execute_plan_automatically, plan.plan_id)
                self._emit("auto_session.execution_result", outcome, str(outcome.get("reason", "PASS")))
        except Exception as exc:
            self._session.degrade(type(exc).__name__)
            self._emit("runtime.error", {"error_type": type(exc).__name__}, "FAIL_CLOSED")
        finally:
            if self._realtime is not None:
                self._realtime.state.metrics.record_strategy(started, now_ms(), created)

    @serialized_action
    def set_environment(self, environment: str) -> dict[str, Any]:
        try:
            requested = EnvironmentState(environment.upper())
        except ValueError as exc:
            raise ServiceError("INVALID_ENVIRONMENT", 422) from exc
        if requested is EnvironmentState.LIVE:
            raise ServiceError("LIVE_NOT_CONFIGURED", 423, "Live Trading is locked and not implemented")
        return self._transition("SET_ENVIRONMENT_DEMO", environment=EnvironmentState.DEMO)

    @serialized_action
    def set_mode(self, mode: str) -> dict[str, Any]:
        try:
            requested = TradingMode(mode.upper())
        except ValueError as exc:
            raise ServiceError("INVALID_TRADING_MODE", 422) from exc
        control = self._control.get()
        if requested is TradingMode.AUTO and not control.auto_demo_enabled:
            raise ServiceError("AUTO_DEMO_DISABLED", 423)
        if requested is TradingMode.AUTO and not self.client_stream_fresh():
            raise ServiceError("CONTROL_STREAM_NOT_FRESH", 423)
        changes: dict[str, Any] = {"trading_mode": requested}
        if requested is TradingMode.STOPPED:
            changes.update(
                execution_state=ExecutionState.DISARMED,
                agent_runtime_state=AgentRuntimeState.STOPPED,
            )
        return self._transition(f"SET_MODE_{requested.value}", **changes)

    @serialized_action
    def start_agent(self) -> dict[str, Any]:
        control = self._control.get()
        if control.kill_switch_active:
            raise ServiceError("KILL_SWITCH_ACTIVE", 423)
        if control.trading_mode is TradingMode.STOPPED:
            raise ServiceError("SELECT_RUNTIME_MODE_FIRST", 409)
        return self._transition("START_AGENT", agent_runtime_state=AgentRuntimeState.RUNNING)

    @serialized_action
    def stop_agent(self) -> dict[str, Any]:
        return self._transition(
            "STOP_AGENT", agent_runtime_state=AgentRuntimeState.STOPPED,
            execution_state=ExecutionState.DISARMED,
            session_state=SessionState.STOPPED,
            trading_mode=TradingMode.STOPPED,
            auto_demo_enabled=False,
        )

    def refresh_health(self) -> dict[str, Any]:
        try:
            health = self._run_core(self._api.get_health)
            status = health.get("system_capability", {}).get("status")
            connection = ConnectionState.CONNECTED if status == "READY" else ConnectionState.STALE
        except Exception as exc:
            health = {
                "system_capability": {"status": "NOT_READY"},
                "trading_eligibility": {
                    "eligible": False, "reason": "DATA_UNAVAILABLE",
                    "blocking_reasons": ["DATA_UNAVAILABLE"],
                },
                "error_type": type(exc).__name__,
            }
            connection = ConnectionState.DISCONNECTED
        health = sanitize_for_browser(health)
        self._last_health = health
        self._last_health_at_ms = now_ms()
        current = self._control.get()
        if current.connection_state is not connection:
            self._transition("BACKEND_CONNECTION_UPDATE", connection_state=connection)
        self._emit("health.updated", health)
        return health

    @serialized_action
    def arm(self) -> dict[str, Any]:
        control = self._control.get()
        if not self.client_stream_fresh():
            raise ServiceError("CONTROL_STREAM_NOT_FRESH", 423)
        if control.environment is not EnvironmentState.DEMO:
            raise ServiceError("LIVE_NOT_CONFIGURED", 423)
        if control.kill_switch_active:
            raise ServiceError("KILL_SWITCH_ACTIVE", 423)
        if control.agent_runtime_state is not AgentRuntimeState.RUNNING:
            raise ServiceError("AGENT_NOT_RUNNING", 409)
        if control.trading_mode not in {TradingMode.MANUAL_APPROVAL, TradingMode.AUTO}:
            raise ServiceError("MODE_NOT_EXECUTABLE", 409)
        health = self.refresh_health()
        blockers = set(health.get("trading_eligibility", {}).get("blocking_reasons", []))
        blockers.difference_update({"EXECUTION_DISARMED"})
        if health.get("system_capability", {}).get("status") != "READY" or blockers:
            reason = min(blockers) if blockers else "SYSTEM_CAPABILITY_NOT_READY"
            raise ServiceError(reason, 423)
        return self._transition("ARM_DEMO_EXECUTION", execution_state=ExecutionState.ARMED)

    @serialized_action
    def disarm(self) -> dict[str, Any]:
        return self._transition("DISARM_EXECUTION", execution_state=ExecutionState.DISARMED)

    @serialized_action
    def enable_auto_demo(self, confirmation: str) -> dict[str, Any]:
        if confirmation.strip() != AUTO_DEMO_CONFIRMATION:
            raise ServiceError("EXACT_AUTO_DEMO_CONFIRMATION_REQUIRED", 422)
        control = self._control.get()
        if not self.client_stream_fresh():
            raise ServiceError("CONTROL_STREAM_NOT_FRESH", 423)
        if control.environment is not EnvironmentState.DEMO or control.kill_switch_active:
            raise ServiceError("AUTO_DEMO_NOT_ALLOWED", 423)
        return self._transition("ENABLE_AUTO_DEMO", auto_demo_enabled=True)

    @serialized_action
    def disable_auto_demo(self) -> dict[str, Any]:
        changes: dict[str, Any] = {"auto_demo_enabled": False}
        if self._control.get().trading_mode is TradingMode.AUTO:
            changes.update(
                trading_mode=TradingMode.MANUAL_APPROVAL,
                execution_state=ExecutionState.DISARMED,
            )
        return self._transition("DISABLE_AUTO_DEMO", **changes)

    @serialized_action
    def activate_kill_switch(self) -> dict[str, Any]:
        snapshot = self._control.transition(
            "ACTIVATE_KILL_SWITCH", reason="NEW_ENTRIES_BLOCKED_PROTECTION_PRESERVED",
            kill_switch_active=True, auto_demo_enabled=False,
            execution_state=ExecutionState.DISARMED,
            agent_runtime_state=AgentRuntimeState.STOPPED,
            trading_mode=TradingMode.STOPPED,
            session_state=SessionState.STOPPED,
        )
        self._run_core(self._orchestrator.state.activate_kill_switch)
        result = snapshot.as_dict()
        self._emit("risk.kill_switch", result, "NEW_ENTRIES_BLOCKED_PROTECTION_PRESERVED")
        return result

    @serialized_action
    def reset_kill_switch(self, confirmation: str) -> dict[str, Any]:
        if confirmation.strip() != KILL_SWITCH_RESET_CONFIRMATION:
            raise ServiceError("EXACT_KILL_SWITCH_RESET_CONFIRMATION_REQUIRED", 422)
        self._run_core(self._orchestrator.state.reset_kill_switch)
        return self._transition(
            "RESET_KILL_SWITCH", kill_switch_active=False,
            execution_state=ExecutionState.DISARMED,
            agent_runtime_state=AgentRuntimeState.STOPPED,
            trading_mode=TradingMode.STOPPED,
            session_state=SessionState.STOPPED,
        )

    def status(self) -> dict[str, Any]:
        core = self._run_core(self._api.get_status)
        pending = self.plans(limit=200)
        pending_expiries = [
            int(item["expires_at_ms"])
            for item in pending
            if item.get("ui_status") == "PENDING_APPROVAL" and item.get("expires_at_ms") is not None
        ]
        self._last_status = sanitize_for_browser({
            "schema_version": "1.0",
            "control": self.control(),
            "session": self.session_status(),
            "market": self._last_market,
            "core": core,
            "live": {"setup_state": "NOT_CONFIGURED", "execution": "LOCKED"},
            "observability": {
                "connection_freshness_age_seconds": self.client_stream_freshness_age_seconds(),
                "last_scan_at_ms": self._last_scan_at_ms,
                "last_health_at_ms": self._last_health_at_ms,
                "last_account_at_ms": self._last_account_at_ms,
                "account_freshness_age_seconds": (
                    (now_ms() - self._last_account_at_ms) / 1000
                    if self._last_account_at_ms else None
                ),
                "pending_plan_nearest_expiry_ms": min(pending_expiries) if pending_expiries else None,
                "daily_pnl": core.get("daily_pnl"),
                "consecutive_losses": core.get("consecutive_losses"),
                "wallet_exposure_pct": (
                    float(self._last_account.get("exposure", {}).get("wallet_exposure_usdt", 0))
                    / float(self._last_account.get("equity_usdt", 0))
                    if isinstance(self._last_account.get("exposure"), dict)
                    and float(self._last_account.get("equity_usdt", 0)) > 0
                    else None
                ),
                "managed_exposure_usdt": (
                    self._last_account.get("exposure", {}).get("managed_exposure_usdt")
                    if isinstance(self._last_account.get("exposure"), dict) else None
                ),
            },
        })
        return self._last_status

    def account(self) -> dict[str, Any]:
        self._synchronize_account()
        return self._last_account

    def _synchronize_account(self) -> dict[str, Any]:
        projection = sanitize_for_browser(
            self._account_sync.synchronize(
                lambda: self._run_core(self._orchestrator.synchronize_account)
            )
        )
        next_account = dict(projection.get("account", {}))
        next_orders = dict(projection.get("orders", {}))
        next_positions = dict(projection.get("positions", {}))
        next_fills = list(projection.get("fills", []))
        self._last_account_at_ms = now_ms()
        if next_account != self._last_account:
            self._last_account = next_account
            self._emit("account.updated", self._last_account)
        if next_orders != self._last_orders:
            self._last_orders = next_orders
            self._emit("orders.updated", self._last_orders)
        if next_positions != self._last_positions:
            self._last_positions = next_positions
            self._emit("positions.updated", self._last_positions)
        if next_fills != self._last_fills:
            self._last_fills = next_fills
            self._emit("fills.updated", {"fills": self._last_fills})
        return projection

    def scan(self) -> dict[str, Any]:
        self._last_scan = sanitize_for_browser(self._run_core(self._api.scan))
        self._last_scan_at_ms = now_ms()
        self._emit("scanner.updated", self._last_scan)
        return self._last_scan

    def analyze(self, symbol: str) -> dict[str, Any]:
        symbol = symbol.upper()
        if symbol not in self.config.symbols:
            raise ServiceError("SYMBOL_NOT_CONFIGURED", 422)
        result = sanitize_for_browser(self._run_core(self._api.analyze, symbol))
        self._emit("plan.created", result)
        return result

    def scanner_state(self) -> dict[str, Any]:
        return dict(self._last_scan)

    def signals(self, limit: int = 200) -> list[dict[str, Any]]:
        return sanitize_for_browser(self._run_core(self._api.get_signals, limit))

    def plans(self, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        return sanitize_for_browser(self._run_core(self._api.get_trade_plans, status, limit))

    def pending_plans(self) -> list[dict[str, Any]]:
        return sanitize_for_browser(self._run_core(self._api.get_pending_plans))

    def orders(self) -> dict[str, Any]:
        if not self._last_orders:
            self._synchronize_account()
        return self._last_orders

    def positions(self) -> dict[str, Any]:
        if not self._last_positions:
            self._synchronize_account()
        return self._last_positions

    def fills(self) -> list[dict[str, Any]]:
        if not self._last_account:
            self._synchronize_account()
        return list(self._last_fills)

    def trades(self) -> list[dict[str, Any]]:
        return sanitize_for_browser(self._run_core(self._api.get_trades))

    def approval_preview(self, plan_id: str) -> dict[str, Any]:
        self._require_execution_ready()
        result = self._run_core(self._api.approve_plan, plan_id, "")
        self._emit("plan.revalidated", result, str(result.get("reason", "PASS")))
        return sanitize_for_browser(result)

    @serialized_action
    def approve_plan_with_challenge(
        self,
        plan_id: str,
        expected_preview: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_execution_ready()
        result = self._run_core(
            self._orchestrator.approve_plan,
            plan_id,
            APPROVAL_CONFIRMATION,
            expected_preview=expected_preview,
        )
        reason = str(result.get("reason", "PASS"))
        self._emit("plan.approval_result", result, reason)
        if result.get("status") == "REPREVIEW_REQUIRED":
            raise ServiceError("APPROVAL_PREVIEW_CHANGED", 409)
        if result.get("status") == "REJECTED":
            raise ServiceError(reason, 409)
        return sanitize_for_browser(result)

    @serialized_action
    def approve_plan(self, plan_id: str, confirmation: str) -> dict[str, Any]:
        self._require_execution_ready()
        if confirmation.strip() != APPROVAL_CONFIRMATION:
            raise ServiceError("EXACT_DEMO_APPROVAL_REQUIRED", 422)
        result = self._run_core(self._api.approve_plan, plan_id, confirmation)
        self._emit("plan.approval_result", result, str(result.get("reason", "PASS")))
        return sanitize_for_browser(result)

    def reject_plan(self, plan_id: str) -> dict[str, Any]:
        result = self._run_core(self._api.reject_plan, plan_id)
        self._emit("plan.rejected", result, "USER_REJECTED")
        return sanitize_for_browser(result)

    def _require_execution_ready(self) -> None:
        control = self._control.get()
        if not self.client_stream_fresh():
            raise ServiceError("CONTROL_STREAM_NOT_FRESH", 423)
        if control.connection_state is not ConnectionState.CONNECTED:
            raise ServiceError("BACKEND_CONNECTION_NOT_FRESH", 423)
        if control.kill_switch_active:
            raise ServiceError("KILL_SWITCH_ACTIVE", 423)
        if control.agent_runtime_state is not AgentRuntimeState.RUNNING:
            raise ServiceError("AGENT_NOT_RUNNING", 423)
        if control.execution_state is not ExecutionState.ARMED:
            raise ServiceError("EXECUTION_DISARMED", 423)
        if control.trading_mode not in {TradingMode.MANUAL_APPROVAL, TradingMode.AUTO}:
            raise ServiceError("MODE_NOT_EXECUTABLE", 423)

    def run_backtest(self, symbol: str, days: int = 7, walk_forward: bool = False) -> dict[str, Any]:
        symbol = symbol.upper()
        if symbol not in self.config.symbols:
            raise ServiceError("SYMBOL_NOT_CONFIGURED", 422)
        if days not in {7, 30, 90}:
            raise ServiceError("INVALID_BACKTEST_RANGE", 422)
        control = self._control.get()
        if control.execution_state is ExecutionState.ARMED:
            raise ServiceError("BACKTEST_BLOCKED_WHILE_EXECUTION_ARMED", 423)
        if (
            control.agent_runtime_state is AgentRuntimeState.RUNNING
            and not self._backtests.concurrent_with_runtime_supported()
        ):
            raise ServiceError("BACKTEST_BACKEND_CONCURRENCY_UNAVAILABLE", 409)
        result = self._backtests.run(symbol, days, walk_forward)
        self._emit("backtest.completed", {"symbol": symbol, "days": days, "walk_forward": walk_forward})
        return sanitize_for_browser(result)

    def settings(self) -> dict[str, Any]:
        return sanitize_for_browser({
            "symbols": list(self.config.symbols),
            "environment": "demo",
            "runtime": {
                "scan_interval_seconds": self._control.get().scan_interval_seconds,
                "auto_demo_default": False,
            },
            "runtime_display": {
                "realtime_market_enabled": bool(
                    self.config.rules.get("realtime", {}).get("enabled", False)
                ),
                "market_source": "OKX Public WebSocket",
                "strategy_trigger": "Confirmed 1m Candle",
                "confirmations": list(
                    self.config.rules.get("timeframes", {}).get("confirmation", [])
                ),
                "account_sync_interval_seconds": self.config.rules.get(
                    "runtime", {}
                ).get("account_sync_interval_seconds"),
                "targeted_reconciliation": "ENABLED",
            },
            "risk": dict(self.config.rules["risk"]),
            "scalping": dict(self.config.rules["scalping"]),
            "execution": {
                key: value for key, value in self.config.rules["execution"].items()
                if key not in {"api_key", "secret", "passphrase", "token"}
            },
            "live": "LOCKED_NOT_IMPLEMENTED",
        })

    @serialized_action
    def update_runtime_settings(self, scan_interval_seconds: float) -> dict[str, Any]:
        if not 5 <= scan_interval_seconds <= 3600:
            raise ServiceError("INVALID_SCAN_INTERVAL", 422)
        return self._transition(
            "UPDATE_SCAN_INTERVAL", scan_interval_seconds=float(scan_interval_seconds),
        )

    def logs(self, limit: int = 200) -> list[str]:
        safe_limit = max(1, min(int(limit), 1000))
        path = self.config.root / "logs" / "trading_agent.log"
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = deque(handle, maxlen=safe_limit)
        return [sanitize_for_browser(line.rstrip()) for line in lines]

    def audit_log(self, limit: int = 200) -> list[dict[str, Any]]:
        return self._control.list_audit(limit)

    def dashboard_snapshot(self) -> dict[str, Any]:
        status = dict(self._last_status) if self._last_status else {
            "schema_version": "1.0",
            "control": self.control(),
            "core": {
                "environment": self.config.environment,
                "backend": self.config.backend,
                "runtime_mode": RuntimeMode.STOPPED.value,
            },
            "live": {"setup_state": "NOT_CONFIGURED", "execution": "LOCKED"},
        }
        status["control"] = self.control()
        return {
            "schema_version": "1.0",
            "timestamp_ms": now_ms(),
            "sequence": self.latest_sequence,
            "status": status,
            "health": self._last_health,
            "account": self._last_account,
            "scanner": self._last_scan,
            "signals": self.signals(50),
            "plans": self.plans(limit=100),
            "orders": self._last_orders,
            "positions": self._last_positions,
            "fills": self._last_fills,
            "trades": self.trades(),
            "market": self._last_market,
            "session": self.session_status(),
        }

    def _scheduler_loop(self) -> None:
        while not self._scheduler_stop.is_set():
            interval = float(
                self.config.rules.get("runtime", {}).get("account_sync_interval_seconds", 3)
            )
            try:
                self.runtime_tick()
            except Exception as exc:
                self.handle_runtime_failure(exc)
            self._scheduler_stop.wait(interval)

    def runtime_tick(self) -> None:
        """One deterministic scheduler iteration, exposed for integration tests."""
        control = self._control.get()
        if self._realtime is not None:
            stale = self._realtime.watchdog()
            self._last_market = sanitize_for_browser(self._realtime.status())
            if stale and control.session_state is SessionState.RUNNING:
                self._session.degrade("REALTIME_MARKET_STALE")
                self._emit("market.stale", {"symbols": list(stale)}, "FAIL_CLOSED")
        if self._last_health_at_ms is None or now_ms() - self._last_health_at_ms >= 15_000:
            self.refresh_health()
        self._synchronize_account()
        recovery = self._run_core(self._orchestrator.recover)
        self._emit("orders.reconciled", {"results": recovery})
        control = self._control.get()
        positions = self._run_core(self._orchestrator.trade_store.managed_positions)
        if (
            control.session_state is SessionState.RUNNING
            and any(item.protection_state != "PROTECTED" for item in positions)
        ):
            self._session.degrade("POSITION_UNPROTECTED")
            self._emit("risk.position_unprotected", {}, "FAIL_CLOSED")
            control = self._control.get()
        if control.session_state is SessionState.FLATTENING:
            result = self._session.continue_flatten()
            self._emit("session.flatten", result, str(result.get("status", "FLATTENING")))
            return
        if control.agent_runtime_state is not AgentRuntimeState.RUNNING:
            return
        if self._realtime is not None:
            return
        scan = self.scan()
        if (
            control.trading_mode is TradingMode.AUTO
            and control.auto_demo_enabled
            and control.execution_state is ExecutionState.ARMED
            and control.connection_state is ConnectionState.CONNECTED
                and not control.kill_switch_active
                and control.session_state is SessionState.RUNNING
        ):
            self._auto_execute(scan)

    def handle_runtime_failure(self, exc: Exception) -> None:
        current = self._control.get()
        if current.session_state is SessionState.RUNNING:
            try:
                self._session.degrade(type(exc).__name__)
            except Exception:
                self._run_core(self._force_core_safe)
        elif current.agent_runtime_state is not AgentRuntimeState.STOPPED:
            try:
                self._transition(
                    "RUNTIME_FAILURE_FAIL_CLOSED", reason=type(exc).__name__,
                    agent_runtime_state=AgentRuntimeState.DEGRADED,
                    execution_state=ExecutionState.DISARMED,
                    connection_state=ConnectionState.STALE,
                    session_state=SessionState.DEGRADED,
                )
            except Exception:
                self._run_core(self._force_core_safe)
        self._emit("runtime.error", {"error_type": type(exc).__name__}, "FAIL_CLOSED")

    def _auto_execute(self, scan: dict[str, Any]) -> None:
        for result in scan.values():
            control = self._control.get()
            if not (
                control.trading_mode is TradingMode.AUTO
                and control.auto_demo_enabled
                and control.execution_state is ExecutionState.ARMED
                and control.connection_state is ConnectionState.CONNECTED
                and control.agent_runtime_state is AgentRuntimeState.RUNNING
                and not control.kill_switch_active
                and control.session_state is SessionState.RUNNING
            ):
                return
            if not isinstance(result, dict):
                continue
            if result.get("decision") != "BUY" or not result.get("risk_approved"):
                continue
            plan_id = str(result.get("plan_id", ""))
            if not plan_id:
                continue
            outcome = sanitize_for_browser(
                self._run_core(self._orchestrator.execute_plan_automatically, plan_id)
            )
            self._emit("auto_session.execution_result", outcome, str(outcome.get("reason", "PASS")))

    def close(self) -> None:
        if self._closed:
            return
        self._scheduler_stop.set()
        if self._scheduler is not None:
            self._scheduler.join(timeout=5)
        if self._realtime is not None:
            self._realtime.close()
        self._account_sync.close()
        self._market_event_stop.set()
        self._event_worker.join(timeout=5)
        try:
            self._run_core(self._orchestrator.state.disarm_execution)
            self._control.transition(
                "SERVICE_SHUTDOWN_SAFE_RESET", actor="system", reason="SAFE_SHUTDOWN",
                execution_state=ExecutionState.DISARMED,
                agent_runtime_state=AgentRuntimeState.STOPPED,
                trading_mode=TradingMode.STOPPED,
                auto_demo_enabled=False,
                connection_state=ConnectionState.DISCONNECTED,
                session_state=SessionState.STOPPED,
            )
            self._run_core(self._orchestrator.close)
        finally:
            self._closed = True
            self._worker.shutdown(wait=True, cancel_futures=True)
            self._backtests.close()
            self._control.close()

    def __enter__(self) -> TradingService:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
