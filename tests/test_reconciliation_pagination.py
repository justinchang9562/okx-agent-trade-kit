from __future__ import annotations

import pytest

from execution.order_manager import OrderManager
from execution.order_state import OrderState
from storage.trade_store import now_ms
from tests.test_core_hardening import Executor, LifecycleBackend, plan_for


class PagedFillsBackend(LifecycleBackend):
    def __init__(self, pages: list[list[dict]], *, complete_capability: bool = True) -> None:
        super().__init__()
        self.pages = pages
        self.complete_capability = complete_capability

    def capabilities(self):
        return {
            "attached_tp_sl": True,
            "client_order_id": True,
            "fill_order_lookup": False,
            "fills_pagination": self.complete_capability,
            "fills_time_window": True,
            "fills_archive": True,
        }

    def get_fills(self, symbol=None, *, after=None, **_kwargs):
        if not self.complete_capability:
            return {"data": {"data": self.pages[0]}}
        if after is None:
            index = 0
        else:
            index = next(
                (i + 1 for i, page in enumerate(self.pages) if str(page[-1].get("billId")) == str(after)),
                len(self.pages),
            )
        return {"data": {"data": self.pages[index] if index < len(self.pages) else []}}


def filler(page: int, count: int = 100) -> list[dict]:
    return [
        {
            "billId": f"{page}-{index}", "ordId": f"unrelated-{page}-{index}",
            "side": "sell", "fillSz": ".001", "fillPx": "99", "fillTime": str(now_ms()),
        }
        for index in range(count)
    ]


def prepared_manager(tmp_path, long_signal, backend):
    from storage.trade_store import TradeStore

    store = TradeStore(tmp_path / "paged.db")
    plan = plan_for(long_signal)
    store.save_plan(plan)
    manager = OrderManager(Executor(backend), store)
    manager.submit(plan, "CONFIRM DEMO ORDER")
    backend.remote = {
        "ordId": "entry-1", "clOrdId": plan.plan_id, "state": "filled",
        "accFillSz": ".01", "avgPx": "100",
    }
    backend.protection = [{
        "clOrdId": plan.plan_id, "algoId": "exit-1", "state": "live", "sz": ".01",
        "slTriggerPx": str(plan.stop), "tpTriggerPx": str(plan.take_profit),
    }]
    manager.reconcile_plan(plan.plan_id)
    return plan, store, manager


def test_target_exit_fill_on_third_page_closes_position(tmp_path, long_signal) -> None:
    target = {
        "billId": "target", "ordId": "exit-1", "side": "sell", "fillSz": ".01",
        "fillPx": "101", "fee": "-.01", "feeCcy": "USDT", "fillTime": str(now_ms() + 1000),
    }
    backend = PagedFillsBackend([filler(1), filler(2), [target]])
    plan, store, manager = prepared_manager(tmp_path, long_signal, backend)
    result = manager.reconcile_managed_positions()[0]
    assert result["state"] == OrderState.CLOSED.value
    assert store.get_trades()[0]["net_pnl"] is None  # entry fee was unavailable; PnL stays fail-closed.
    assert store.reconciliation_cursor(
        plan.symbol, f"protective_exit_fills:{plan.plan_id}",
    )["last_fill_id"] == "target"
    assert manager.reconcile_managed_positions() == []
    store.close()


def test_duplicate_partial_fills_are_deduplicated(tmp_path, long_signal) -> None:
    first = {
        "billId": "same", "ordId": "exit-1", "side": "sell", "fillSz": ".004",
        "fillPx": "101", "fee": "-.01", "feeCcy": "USDT", "fillTime": str(now_ms() + 1000),
    }
    page_one = filler(1, 99) + [first]
    second = first | {"billId": "second", "fillSz": ".006", "fillTime": str(now_ms() + 2000)}
    backend = PagedFillsBackend([page_one, [first, second]])
    _plan, store, manager = prepared_manager(tmp_path, long_signal, backend)
    assert manager.reconcile_managed_positions()[0]["state"] == OrderState.CLOSED.value
    assert store.managed_positions(active_only=False)[0].exit_filled_quantity == .01
    assert len(store.reconciled_exit_fills(_plan.plan_id)) == 2
    store.close()


def test_partial_exit_accumulates_across_restarts_without_duplicate_fee(tmp_path, long_signal) -> None:
    timestamp = now_ms() + 1000
    first = {
        "billId": "first", "ordId": "exit-1", "side": "sell", "fillSz": ".004",
        "fillPx": "101", "fee": "-.01", "feeCcy": "USDT", "fillTime": str(timestamp),
    }
    second = first | {
        "billId": "second", "fillSz": ".006", "fee": "-.02", "fillTime": str(timestamp + 1000),
    }
    backend = PagedFillsBackend([[first]])
    plan, store, manager = prepared_manager(tmp_path, long_signal, backend)

    partial = manager.reconcile_managed_positions()[0]
    assert partial["state"] == OrderState.EXIT_PARTIALLY_FILLED.value
    assert partial["filled_size"] == .004

    backend.pages = [[first, second]]
    restarted = OrderManager(Executor(backend), store)
    closed = restarted.reconcile_managed_positions()[0]
    assert closed["state"] == OrderState.CLOSED.value
    assert len(store.reconciled_exit_fills(plan.plan_id)) == 2
    position = store.managed_positions(active_only=False)[0]
    assert position.exit_filled_quantity == .01
    assert position.exit_fee_breakdown_json == '{"USDT": 0.03}'
    store.close()


def test_incomplete_fill_history_never_closes_position(tmp_path, long_signal) -> None:
    backend = PagedFillsBackend([[]], complete_capability=False)
    _plan, store, manager = prepared_manager(tmp_path, long_signal, backend)
    result = manager.reconcile_managed_positions()[0]
    assert result["reason"] == "RECOVERY_DATA_INSUFFICIENT"
    assert store.managed_positions()[0].state == OrderState.FILLED.value
    store.close()


def test_negative_okx_fees_are_positive_costs_and_quote_currency_drives_pnl(
    tmp_path, long_signal,
) -> None:
    backend = PagedFillsBackend([[]])
    plan, store, manager = prepared_manager(tmp_path, long_signal, backend)
    backend.pages = [[{
        "billId": "entry-fee", "ordId": "entry-1", "side": "buy", "fillSz": ".01",
        "fillPx": "100", "fee": "-.01", "feeCcy": "USDT", "fillTime": str(now_ms()),
    }]]
    manager.reconcile_plan(plan.plan_id)
    assert store.order_for_plan(plan.plan_id)["fee"] == .01
    assert store.get_trades()[0]["fees"] == .01

    backend.pages = [[{
        "billId": "exit-fee", "ordId": "exit-1", "side": "sell", "fillSz": ".01",
        "fillPx": "101", "fee": "-.02", "feeCcy": "USDT", "fillTime": str(now_ms() + 1000),
    }]]
    assert manager.reconcile_managed_positions()[0]["state"] == OrderState.CLOSED.value
    trade = store.get_trades()[0]
    assert trade["fees"] == .03
    assert trade["net_pnl"] == pytest.approx(-.02)
    store.close()


def test_non_quote_fee_currency_never_gets_invented_as_usdt(tmp_path, long_signal) -> None:
    backend = PagedFillsBackend([[{
        "billId": "exit-btc-fee", "ordId": "exit-1", "side": "sell", "fillSz": ".01",
        "fillPx": "101", "fee": "-.00001", "feeCcy": "BTC", "fillTime": str(now_ms() + 1000),
    }]])
    plan, store, manager = prepared_manager(tmp_path, long_signal, backend)
    result = manager.reconcile_managed_positions()[0]
    assert result["state"] == OrderState.CLOSED.value
    assert result["fee_status"] == "CURRENCY_CONVERSION_REQUIRED"
    trade = store.get_trades()[0]
    assert trade["net_pnl"] is None
    assert trade["fee_status"] == "CURRENCY_CONVERSION_REQUIRED"
    assert store.managed_positions(active_only=False)[0].exit_fee_breakdown_json == '{"BTC": 1e-05}'
    store.close()
