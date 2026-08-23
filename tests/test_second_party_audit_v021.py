from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import replace
from types import MethodType

import pytest

from data.models import AccountSnapshot, Balance
from execution.demo_executor import DemoExecutor
from execution.errors import PreSubmitRejectedError, StateChangedError, SubmissionUncertainError
from execution.mcp_backend import MCPError, StdioMCPClient
from execution.order_manager import OrderManager
from execution.order_state import OrderState
from risk.daily_limits import DailyRiskState
from risk.exposure import build_exposure_snapshot
from risk.price_quantization import quantize_long_execution_prices, risk_reward
from risk.risk_manager import RiskManager
from storage.database import LATEST_SCHEMA_VERSION, connect, schema_version
from storage.trade_store import TradeStore, now_ms
from tests.test_approval_revalidation import agent
from tests.test_core_hardening import LifecycleBackend, plan_for, setup_manager
from trading_agent import __version__
from trading_agent.config import load_config
from trading_agent.state import AgentState, RuntimeMode


def test_stopped_blocks_approval_without_mutating_plan_or_protection(tmp_path, market, account) -> None:
    with agent(tmp_path, market, account) as orchestrator:
        plan = orchestrator.analyze("BTC-USDT")
        protected = replace(plan, plan_id="existing-protected-position")
        orchestrator.trade_store.save_plan(protected)
        orchestrator.trade_store.upsert_managed_position(
            protected.plan_id, "entry-existing", protected.symbol, .01, protected.entry,
            OrderState.FILLED.value, "PROTECTED", ["sl-existing", "tp-existing"],
        )
        orchestrator.stop_trading()
        result = orchestrator.approve_plan(plan.plan_id, "CONFIRM DEMO ORDER")
        assert result["reason"] == "TRADING_STOPPED"
        assert orchestrator.trade_store.get_plan(plan.plan_id).status == OrderState.PLANNED.value
        existing = orchestrator.trade_store.managed_positions()[0]
        assert existing.protection_state == "PROTECTED"
        assert existing.protective_order_ids_json == '["sl-existing", "tp-existing"]'


def test_stopped_final_executor_guard_never_calls_backend(long_signal) -> None:
    backend = LifecycleBackend()
    state = AgentState(runtime_mode=RuntimeMode.STOPPED)
    with pytest.raises(PreSubmitRejectedError, match="TRADING_STOPPED"):
        DemoExecutor(backend, state.require_new_entry_allowed).execute(plan_for(long_signal))
    assert backend.place_calls == 0


def test_final_execution_prices_are_tick_safe_and_drive_risk_sizing(tmp_path, market, account) -> None:
    with agent(tmp_path, market, account) as orchestrator:
        plan = orchestrator.analyze("BTC-USDT")
        fresh = orchestrator.strategy.analyze(market)
        orchestrator.strategy.analyze = lambda snapshot: replace(
            fresh, suggested_take_profit=fresh.entry_price + 6, risk_reward=3,
        )
        preview = orchestrator.approve_plan(plan.plan_id)
        entry = preview["current_executable_price"]
        stop = preview["final_stop"]
        target = preview["final_take_profit"]
        assert entry / market.instrument.tick_size == pytest.approx(round(entry / market.instrument.tick_size))
        assert stop / market.instrument.tick_size == pytest.approx(round(stop / market.instrument.tick_size))
        assert target / market.instrument.tick_size == pytest.approx(round(target / market.instrument.tick_size))
        assert preview["final_risk_reward"] == pytest.approx(risk_reward(entry, stop, target))
        assert preview["final_risk_amount"] == pytest.approx(preview["position_size"] * (entry - stop))


def test_ask_price_degradation_below_minimum_rr_is_rejected(tmp_path, market, account) -> None:
    with agent(tmp_path, market, account) as orchestrator:
        plan = orchestrator.analyze("BTC-USDT")
        result = orchestrator.approve_plan(plan.plan_id)
        assert result["reason"] == "RISK_REWARD_BELOW_MINIMUM"


def test_rr_is_revalidated_after_decimal_tick_rounding(long_signal, market, account) -> None:
    entry, stop, target = quantize_long_execution_prices(100.01, 99.01, 101.51, 0.1)
    rounded = replace(
        long_signal, entry_price=entry, suggested_stop=stop,
        suggested_take_profit=target, risk_reward=risk_reward(entry, stop, target),
    )
    assert RiskManager({
        "environment": "demo", "market": {"stale_after_seconds": 120},
        "trade": {"minimum_risk_reward": 1.5},
        "scalping": {"max_spread_pct": .1, "minimum_signal_score": 7,
                     "minimum_signal_strength": .7, "cooldown_seconds": 0},
        "risk": {"max_daily_loss_pct": .03, "max_consecutive_losses": 3,
                 "max_open_positions": 2, "max_total_exposure_pct": .9},
    }).evaluate(rounded, market, account, DailyRiskState()).reason == "RISK_REWARD_BELOW_MINIMUM"


def test_reserved_entries_consume_slots_and_notional(tmp_path, long_signal) -> None:
    store = TradeStore(tmp_path / "reserved.db")
    plans = [replace(plan_for(long_signal), plan_id=f"reserved-{index}") for index in range(2)]
    for plan in plans:
        store.save_plan(plan)
        assert store.approve_and_create_order(plan, plan.plan_id, 100)
    assert store.position_slots_in_use() == 2
    assert store.daily_state().open_position_count == 2
    assert store.reserved_entry_notional() == pytest.approx(2.0)
    rules = load_config().rules
    assert RiskManager(rules).evaluate(
        long_signal, _market_for(long_signal), _account_for(long_signal), store.daily_state(),
    ).reason == "MAX_OPEN_POSITIONS_REACHED"
    store.close()


def _market_for(signal):
    from data.models import Instrument, MarketSnapshot
    return MarketSnapshot(
        signal.symbol, signal.timestamp_ms, signal.entry_price, signal.entry_price - .01,
        signal.entry_price + .01, 1_000, {}, Instrument(signal.symbol, "BTC", "USDT", .00001, .00001, .1),
    )


def _account_for(signal):
    return AccountSnapshot(signal.timestamp_ms, 1_000, 1_000, (Balance("USDT", 1_000, 1_000),))


def test_entry_to_exit_closes_position_trade_lifecycle_and_releases_slot(tmp_path, long_signal) -> None:
    store = TradeStore(tmp_path / "close.db")
    plan = plan_for(long_signal)
    store.save_plan(plan)
    store.approve_and_create_order(plan, plan.plan_id, 100)
    store.transition_order(plan.plan_id, OrderState.SUBMITTED.value)
    store.transition_order(plan.plan_id, OrderState.FILLED.value, filled_size=.01,
                           average_fill_price=100, filled_at_ms=now_ms())
    store.upsert_managed_position(
        plan.plan_id, "entry-1", plan.symbol, .01, 100,
        OrderState.FILLED.value, "PROTECTED", ["sl-1", "tp-1"],
    )
    store.record_entry_fill(plan, "entry-1", now_ms(), 100, .01, .01, 0, 0)
    store.close_trade(plan.plan_id, now_ms() + 1_000, 101, .01, "exit-1")
    position = store.managed_positions(active_only=False)[0]
    trade = store.get_trades()[0]
    assert position.state == OrderState.CLOSED.value and position.exit_order_id == "exit-1"
    assert position.protective_order_ids_json == '["sl-1", "tp-1"]'
    assert store.order_for_plan(plan.plan_id)["state"] == OrderState.CLOSED.value
    assert trade["exit_order_id"] == "exit-1" and trade["net_pnl"] == pytest.approx(-.01)
    assert store.position_slots_in_use() == 0
    store.close()


def test_linked_protective_exit_fill_is_reconciled_to_closed(tmp_path, long_signal) -> None:
    backend = LifecycleBackend()
    plan, store, manager = setup_manager(tmp_path, long_signal, backend)
    manager.submit(plan, "CONFIRM DEMO ORDER")
    backend.remote = {"ordId": "okx-1", "clOrdId": plan.plan_id, "state": "filled",
                      "accFillSz": ".01", "avgPx": "100"}
    backend.protection = [{"clOrdId": plan.plan_id, "algoId": "exit-1"}]
    backend.fills = [{"ordId": "okx-1", "side": "buy", "fillSz": ".01",
                      "fillPx": "100", "fee": ".01", "fillTime": str(now_ms())}]
    manager.reconcile_plan(plan.plan_id)
    backend.fills.append({"ordId": "exit-1", "side": "sell", "fillSz": ".004",
                          "fillPx": "101", "fee": ".01", "fillTime": str(now_ms() + 1000)})
    partial = manager.reconcile_managed_positions()[0]
    assert partial["state"] == "EXIT_PARTIALLY_FILLED"
    assert store.managed_positions()[0].state == OrderState.EXIT_PARTIALLY_FILLED.value
    backend.fills.append({"ordId": "exit-1", "side": "sell", "fillSz": ".006",
                          "fillPx": "101", "fee": ".01", "fillTime": str(now_ms() + 2000)})
    result = manager.reconcile_managed_positions()[0]
    assert result["state"] == OrderState.CLOSED.value
    assert store.managed_positions(active_only=False)[0].state == OrderState.CLOSED.value
    assert store.position_slots_in_use() == 0
    store.close()


def test_order_transition_uses_compare_and_swap_and_blocks_backward_state(tmp_path, long_signal) -> None:
    path = tmp_path / "cas.db"
    first = TradeStore(path)
    plan = plan_for(long_signal)
    first.save_plan(plan)
    first.approve_and_create_order(plan, plan.plan_id, 100)
    first.transition_order(plan.plan_id, OrderState.SUBMITTED.value)
    second = TradeStore(path)
    first.transition_order(plan.plan_id, OrderState.OPEN.value, expected_state=OrderState.SUBMITTED.value)
    with pytest.raises(StateChangedError, match="ORDER_STATE_CHANGED"):
        second.transition_order(
            plan.plan_id, OrderState.PARTIALLY_FILLED.value,
            expected_state=OrderState.SUBMITTED.value,
        )
    with pytest.raises(RuntimeError, match="INVALID_ORDER_TRANSITION"):
        first.transition_order(plan.plan_id, OrderState.SUBMITTED.value)
    first.close()
    second.close()


def test_concurrent_cas_writers_allow_exactly_one_transition(tmp_path, long_signal) -> None:
    path = tmp_path / "concurrent-cas.db"
    stores = [TradeStore(path), TradeStore(path)]
    plan = plan_for(long_signal)
    stores[0].save_plan(plan)
    stores[0].approve_and_create_order(plan, plan.plan_id, 100)
    stores[0].transition_order(plan.plan_id, OrderState.SUBMITTED.value)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def transition(store, target):
        barrier.wait()
        try:
            store.transition_order(plan.plan_id, target, expected_state=OrderState.SUBMITTED.value)
            outcomes.append("won")
        except StateChangedError:
            outcomes.append("lost")

    threads = [
        threading.Thread(target=transition, args=(stores[0], OrderState.OPEN.value)),
        threading.Thread(target=transition, args=(stores[1], OrderState.PARTIALLY_FILLED.value)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["lost", "won"]
    for store in stores:
        store.close()


class ClassifiedExecutor:
    def __init__(self, backend, error):
        self.backend = backend
        self.error = error

    def execute(self, plan):
        raise self.error


def test_pre_submit_rejection_is_not_submission_unknown(tmp_path, long_signal) -> None:
    backend = LifecycleBackend()
    plan = plan_for(long_signal)
    store = TradeStore(tmp_path / "classified.db")
    store.save_plan(plan)
    manager = OrderManager(ClassifiedExecutor(backend, PreSubmitRejectedError("TRADING_STOPPED")), store)
    with pytest.raises(RuntimeError, match="PRE_SUBMIT_REJECTED"):
        manager.submit(plan, "CONFIRM DEMO ORDER")
    row = store.order_for_plan(plan.plan_id)
    assert row["state"] == OrderState.REJECTED.value
    assert row["last_error"].startswith("PRE_SUBMIT_REJECTED")
    store.close()


def test_uncertain_submission_remains_reserved_until_reconciled(tmp_path, long_signal) -> None:
    backend = LifecycleBackend()
    plan = plan_for(long_signal)
    store = TradeStore(tmp_path / "unknown.db")
    store.save_plan(plan)
    manager = OrderManager(ClassifiedExecutor(backend, SubmissionUncertainError("MCP_EXITED")), store)
    with pytest.raises(RuntimeError, match="SUBMISSION_UNKNOWN"):
        manager.submit(plan, "CONFIRM DEMO ORDER")
    assert store.position_slots_in_use() == 1
    assert store.reserved_entry_notional() > 0
    store.close()


def test_material_unpriced_asset_fails_closed_but_configured_dust_is_ignored(
    long_signal, market, account,
) -> None:
    wallet = replace(account, balances=(Balance("XYZ", 1, 1), Balance("DUST", 1e-9, 1e-9)))
    exposure = build_exposure_snapshot(wallet, {}, 0, dust_quantity=1e-8)
    assert exposure.status == "UNKNOWN" and exposure.unpriced_currencies == ("XYZ",)
    assert exposure.ignored_dust_currencies == ("DUST",)
    rules = {
        "environment": "demo", "market": {"stale_after_seconds": 120},
        "trade": {"minimum_risk_reward": 1.5},
        "scalping": {"max_spread_pct": .1, "minimum_signal_score": 7,
                     "minimum_signal_strength": .7, "cooldown_seconds": 0},
        "risk": {"max_daily_loss_pct": .03, "max_consecutive_losses": 3,
                 "max_open_positions": 2, "max_total_exposure_pct": .9},
    }
    assert RiskManager(rules).evaluate(
        long_signal, market, account, DailyRiskState(), exposure=exposure,
    ).reason == "EXPOSURE_UNKNOWN"
    dust_only = build_exposure_snapshot(replace(account, balances=(Balance("DUST", 1e-9, 1e-9),)), {}, 0,
                                        dust_quantity=1e-8)
    assert dust_only.status == "KNOWN"


def test_health_separates_capability_from_high_exposure_eligibility(
    tmp_path, market, account, monkeypatch,
) -> None:
    wallet = replace(account, equity_usdt=100, available_usdt=100,
                     balances=(Balance("BTC", 1, 1), Balance("USDT", 100, 100)))
    with agent(tmp_path, market, wallet) as orchestrator:
        monkeypatch.setattr(
            "trading_agent.orchestrator.run_health",
            lambda config, adapter: {"system_capability": {"status": "READY", "version": __version__}},
        )
        health = orchestrator.get_health()
        assert health["system_capability"]["status"] == "READY"
        assert not health["trading_eligibility"]["eligible"]
        assert health["trading_eligibility"]["reason"] == "MAX_TOTAL_EXPOSURE_REACHED"
        assert health["live_trading"] == "LOCKED_NOT_IMPLEMENTED"


def test_schema_migration_is_versioned_and_preserves_existing_data(tmp_path) -> None:
    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(path)
    legacy.executescript("""
      CREATE TABLE signals (id INTEGER PRIMARY KEY, timestamp_ms INTEGER NOT NULL, symbol TEXT NOT NULL,
        score INTEGER NOT NULL, confidence REAL NOT NULL, decision TEXT NOT NULL, reasons_json TEXT NOT NULL);
      CREATE TABLE trades (id INTEGER PRIMARY KEY, timestamp_ms INTEGER NOT NULL, environment TEXT NOT NULL,
        backend TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, entry REAL NOT NULL, exit REAL,
        size REAL NOT NULL, stop REAL NOT NULL, take_profit REAL NOT NULL, fees REAL, slippage REAL,
        pnl REAL, holding_time_seconds REAL, strategy TEXT NOT NULL, signal_score INTEGER NOT NULL);
      CREATE TABLE managed_positions (plan_id TEXT PRIMARY KEY, order_id TEXT, symbol TEXT NOT NULL,
        quantity REAL NOT NULL, entry_price REAL, state TEXT NOT NULL, protection_state TEXT NOT NULL,
        opened_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL, closed_at_ms INTEGER);
      INSERT INTO signals VALUES (1, 1, 'BTC-USDT', 8, .8, 'BUY', '[]');
    """)
    legacy.commit()
    legacy.close()
    migrated = connect(path)
    columns = {row["name"] for row in migrated.execute("PRAGMA table_info(managed_positions)")}
    assert schema_version(migrated) == LATEST_SCHEMA_VERSION
    assert {"exit_order_id", "protective_order_ids_json"}.issubset(columns)
    assert migrated.execute("SELECT COUNT(*) count FROM signals").fetchone()["count"] == 1
    migrated.close()


def test_stdio_mcp_requests_are_serialized() -> None:
    client = StdioMCPClient.__new__(StdioMCPClient)
    client.timeout = 1
    client._next_id = 1
    client._request_lock = threading.Lock()
    active = 0
    maximum = 0
    ids: list[int] = []
    guard = threading.Lock()

    def write(_self, message):
        ids.append(message["id"])

    def read(_self, request_id):
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
        time.sleep(.02)
        with guard:
            active -= 1
        return {"id": request_id}

    client._write = MethodType(write, client)
    client._read_response = MethodType(read, client)
    threads = [threading.Thread(target=client._request, args=("test", {})) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert maximum == 1 and ids == [1, 2, 3, 4]


def test_mcp_tool_error_does_not_echo_sensitive_stderr_or_payload() -> None:
    client = StdioMCPClient.__new__(StdioMCPClient)
    client._request = lambda method, params: {
        "isError": True, "content": [{"text": "api_key=SHOULD_NOT_APPEAR"}],
    }
    with pytest.raises(MCPError) as raised:
        client.call_tool("spot_place_order", {})
    assert "SHOULD_NOT_APPEAR" not in str(raised.value)


def test_component_version_tracks_remediation_release() -> None:
    assert __version__ == "0.3.0"
