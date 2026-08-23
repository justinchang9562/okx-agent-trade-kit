from __future__ import annotations

from dataclasses import replace

import pytest

from data.models import Balance
from decision.decision_engine import DecisionEngine
from execution.base_backend import BackendStatus
from execution.errors import SubmissionUncertainError
from execution.order_manager import OrderManager
from risk.exposure import build_exposure_snapshot
from risk.position_sizing import SizingResult
from risk.risk_manager import RiskDecision
from storage.trade_store import TradeStore


class LifecycleBackend:
    def __init__(self, mode: str = "ack") -> None:
        self.mode = mode
        self.place_calls = 0
        self.remote: dict | None = None
        self.protection: list[dict] = [{"clOrdId": "PLACEHOLDER"}]
        self.fills: list[dict] = []
        self.cancel_response = {"data": {"data": [{"sCode": "0"}]}}

    def status(self): return BackendStatus("fake", "CONNECTED", True, True)
    def capabilities(self):
        return {
            "attached_tp_sl": True,
            "client_order_id": True,
            "fill_order_lookup": True,
            "fills_pagination": True,
            "fills_time_window": True,
            "fills_archive": True,
        }
    def place_order(self, order):
        self.place_calls += 1
        client_id = order["clOrdId"]
        if self.mode in {"before_timeout", "process_crash"}:
            raise SubmissionUncertainError("MCP_TIMEOUT" if self.mode == "before_timeout" else "MCP_EXITED")
        if self.mode == "accepted_timeout":
            self.remote = {"ordId": "okx-1", "clOrdId": client_id, "state": "live", "accFillSz": "0"}
            raise SubmissionUncertainError("MCP_TIMEOUT")
        return {"data": {"data": [{"ordId": "okx-1", "clOrdId": client_id, "sCode": "0"}]}}
    def get_order_by_client_id(self, symbol, client_order_id):
        if self.remote is None:
            raise RuntimeError("NOT_FOUND")
        return {"data": {"data": [self.remote]}}
    def get_order(self, symbol, order_id): return {"data": {"data": [self.remote]}} if self.remote else {"data": {"data": []}}
    def get_instrument(self, symbol):
        return {"data": {"data": [{"tickSz": "0.1"}]}}
    def get_protection_orders(self, symbol=None): return {"data": {"data": self.protection}}
    def get_fills(self, symbol=None, *, order_id=None, **_kwargs):
        rows = self.fills if order_id is None else [
            row for row in self.fills if str(row.get("ordId", "")) == str(order_id)
        ]
        return {"data": {"data": rows}}
    def cancel_order(self, symbol, order_id): return self.cancel_response


class Executor:
    def __init__(self, backend): self.backend = backend
    def execute(self, plan):
        self.backend.protection = [{
            "clOrdId": plan.plan_id,
            "algoId": "exit-1",
            "state": "live",
            "sz": str(plan.position_size),
            "slTriggerPx": str(plan.stop),
            "tpTriggerPx": str(plan.take_profit),
        }]
        return self.backend.place_order({"clOrdId": plan.plan_id, "sz": str(plan.position_size)})


def plan_for(long_signal):
    return DecisionEngine().build_plan(
        long_signal, RiskDecision(True, "PASS"), SizingResult(True, "PASS", 0.01, 100, 2),
        "demo", "mcp", 1000,
    )


def setup_manager(tmp_path, long_signal, backend):
    store = TradeStore(tmp_path / "lifecycle.db")
    plan = plan_for(long_signal)
    store.save_plan(plan)
    return plan, store, OrderManager(Executor(backend), store)


def test_wallet_assets_are_not_managed_positions(tmp_path, long_signal, account) -> None:
    store = TradeStore(tmp_path / "positions.db")
    wallet = replace(account, balances=(Balance("BTC", 1, 1), Balance("ETH", 1, 1), Balance("USDT", 5000, 5000)))
    assert store.managed_open_count() == 0
    exposure = build_exposure_snapshot(wallet, {"BTC": 100, "ETH": 10}, 0)
    assert exposure.wallet_exposure_usdt == 110
    assert exposure.managed_exposure_usdt == 0
    plan = plan_for(long_signal)
    store.save_plan(plan)
    store.upsert_managed_position(plan.plan_id, "1", "BTC-USDT", .01, 100, "FILLED", "PROTECTED")
    assert wallet.balance("BTC") == 1
    assert store.managed_open_count() == 1
    store.close()


def test_two_agent_managed_positions_reach_limit(tmp_path, long_signal) -> None:
    store = TradeStore(tmp_path / "positions.db")
    first = plan_for(long_signal)
    second = replace(first, plan_id="second-plan", symbol="ETH-USDT")
    store.save_plan(first)
    store.save_plan(second)
    store.upsert_managed_position(first.plan_id, "1", first.symbol, .01, 100, "FILLED", "PROTECTED")
    store.upsert_managed_position(second.plan_id, "2", second.symbol, 1, 10, "PARTIALLY_FILLED", "PROTECTED")
    assert store.daily_state().open_position_count == 2
    store.close()


def test_partial_fill_is_not_filled(tmp_path, long_signal) -> None:
    backend = LifecycleBackend()
    plan, store, manager = setup_manager(tmp_path, long_signal, backend)
    manager.submit(plan, "CONFIRM DEMO ORDER")
    backend.remote = {"ordId": "okx-1", "clOrdId": plan.plan_id, "state": "partially_filled", "accFillSz": "0.004", "avgPx": "100"}
    assert manager.reconcile_plan(plan.plan_id)["state"] == "PARTIALLY_FILLED"
    assert store.order_for_plan(plan.plan_id)["filled_size"] == pytest.approx(.004)
    store.close()


@pytest.mark.parametrize("mode", ["before_timeout", "process_crash"])
def test_transport_failure_never_blindly_retries(tmp_path, long_signal, mode) -> None:
    backend = LifecycleBackend(mode)
    plan, store, manager = setup_manager(tmp_path, long_signal, backend)
    with pytest.raises(RuntimeError, match="SUBMISSION_UNKNOWN"):
        manager.submit(plan, "CONFIRM DEMO ORDER")
    assert backend.place_calls == 1
    assert store.order_for_plan(plan.plan_id)["state"] == "SUBMISSION_UNKNOWN"
    with pytest.raises(RuntimeError, match="DUPLICATE_ORDER"):
        manager.submit(plan, "CONFIRM DEMO ORDER")
    assert backend.place_calls == 1
    store.close()


def test_timeout_after_acceptance_reconciles_without_retry(tmp_path, long_signal) -> None:
    backend = LifecycleBackend("accepted_timeout")
    plan, store, manager = setup_manager(tmp_path, long_signal, backend)
    result = manager.submit(plan, "CONFIRM DEMO ORDER")
    assert result["found"] and result["state"] == "OPEN"
    assert backend.place_calls == 1
    store.close()


def test_restart_recovers_submitted_order(tmp_path, long_signal) -> None:
    backend = LifecycleBackend()
    plan, store, manager = setup_manager(tmp_path, long_signal, backend)
    manager.submit(plan, "CONFIRM DEMO ORDER")
    backend.remote = {"ordId": "okx-1", "clOrdId": plan.plan_id, "state": "live", "accFillSz": "0"}
    recovered = OrderManager(Executor(backend), store).recover_active_orders()
    assert recovered[0]["state"] == "OPEN"
    store.close()


def test_stop_failure_marks_position_unprotected_and_missing_fee_stays_null(tmp_path, long_signal) -> None:
    backend = LifecycleBackend()
    plan, store, manager = setup_manager(tmp_path, long_signal, backend)
    manager.submit(plan, "CONFIRM DEMO ORDER")
    backend.remote = {"ordId": "okx-1", "clOrdId": plan.plan_id, "state": "filled", "accFillSz": "0.01", "avgPx": "101"}
    backend.protection = []
    backend.fills = [{"ordId": "okx-1", "fillSz": "0.01", "fillPx": "101"}]
    result = manager.reconcile_plan(plan.plan_id)
    assert result["state"] == "POSITION_UNPROTECTED"
    assert store.order_for_plan(plan.plan_id)["fee"] is None
    trade = store.get_trades()[0]
    assert trade["fees"] is None and trade["fee_status"] == "UNKNOWN"
    store.close()


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ({"tpTriggerPx": ""}, "TAKE_PROFIT_NOT_VERIFIED"),
        ({"slTriggerPx": ""}, "STOP_LOSS_NOT_VERIFIED"),
        ({"sz": "0.001"}, "PROTECTION_QUANTITY_MISMATCH"),
        ({"slTriggerPx": "1"}, "PROTECTION_PRICE_MISMATCH"),
        ({"state": "cancelled"}, "PROTECTION_NOT_ACTIVE"),
        ({"clOrdId": "unrelated-plan"}, "PROTECTION_NOT_FOUND"),
    ],
)
def test_protection_requires_active_correct_sl_tp_price_and_quantity(
    tmp_path, long_signal, mutation, expected,
) -> None:
    backend = LifecycleBackend()
    plan, store, manager = setup_manager(tmp_path, long_signal, backend)
    manager.submit(plan, "CONFIRM DEMO ORDER")
    backend.remote = {
        "ordId": "okx-1", "clOrdId": plan.plan_id, "state": "filled",
        "accFillSz": "0.01", "avgPx": "100",
    }
    backend.protection[0].update(mutation)
    result = manager.reconcile_plan(plan.plan_id)
    assert result["state"] == "POSITION_UNPROTECTED"
    assert store.managed_positions()[0].protection_state == expected
    store.close()


def test_cancel_rejection_does_not_claim_cancelled(tmp_path, long_signal) -> None:
    backend = LifecycleBackend()
    plan, store, manager = setup_manager(tmp_path, long_signal, backend)
    manager.submit(plan, "CONFIRM DEMO ORDER")
    backend.remote = {"ordId": "okx-1", "clOrdId": plan.plan_id, "state": "live", "accFillSz": "0"}
    manager.reconcile_plan(plan.plan_id)
    backend.cancel_response = {"data": {"data": [{"sCode": "51000"}]}}
    with pytest.raises(RuntimeError, match="CANCEL_REJECTED"):
        manager.cancel(plan.plan_id, "CONFIRM DEMO ORDER")
    assert store.order_for_plan(plan.plan_id)["state"] == "OPEN"
    store.close()
