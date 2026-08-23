from __future__ import annotations

from execution.base_backend import BackendStatus
from execution.errors import SubmissionUncertainError
from execution.order_manager import OrderManager
from execution.order_state import OrderState
from storage.trade_store import TradeStore, now_ms
from tests.test_core_hardening import plan_for


class FlattenBackend:
    def __init__(self) -> None:
        self.exit_calls = 0
        self.remote_exit: dict | None = None
        self.cancelled_protection: list[str] = []

    def status(self):
        return BackendStatus("fake", "CONNECTED", True, True)

    def get_order_by_client_id(self, _symbol, client_order_id):
        if self.remote_exit and self.remote_exit.get("clOrdId") == client_order_id:
            return {"data": {"data": [self.remote_exit]}}
        return {"data": {"data": []}}

    def get_order(self, _symbol, order_id):
        if self.remote_exit and self.remote_exit.get("ordId") == order_id:
            return {"data": {"data": [self.remote_exit]}}
        return {"data": {"data": []}}

    def cancel_order(self, _symbol, _order_id):
        return {"data": {"data": [{"sCode": "0"}]}}

    def cancel_protection_order(self, _symbol, order_id):
        self.cancelled_protection.append(order_id)
        return {"data": {"data": [{"sCode": "0"}]}}


class FlattenExecutor:
    def __init__(self, backend: FlattenBackend, uncertain: bool = False) -> None:
        self.backend = backend
        self.uncertain = uncertain

    def execute_managed_exit(self, symbol: str, quantity: float, client_order_id: str):
        self.backend.exit_calls += 1
        if self.uncertain:
            raise SubmissionUncertainError("MCP_TIMEOUT")
        return {"data": {"data": [{
            "sCode": "0", "ordId": "exit-1", "clOrdId": client_order_id,
            "instId": symbol, "sz": str(quantity),
        }]}}


def prepared_store(tmp_path, long_signal):
    store = TradeStore(tmp_path / "flatten.db")
    plan = plan_for(long_signal)
    store.save_plan(plan)
    store.approve_and_create_order(plan, plan.plan_id, plan.entry)
    store.transition_order(plan.plan_id, OrderState.SUBMITTED.value)
    store.transition_order(
        plan.plan_id, OrderState.FILLED.value, filled_size=plan.position_size,
        average_fill_price=plan.entry, filled_at_ms=now_ms(),
    )
    store.upsert_managed_position(
        plan.plan_id, "entry-1", plan.symbol, plan.position_size, plan.entry,
        OrderState.FILLED.value, "PROTECTED", ["sl-1", "tp-1"],
    )
    store.record_entry_fill(
        plan, "entry-1", now_ms(), plan.entry, plan.position_size, None, 0, 0,
    )
    return plan, store


def test_double_flatten_creates_one_close_intent_and_closes_only_managed_position(
    tmp_path, long_signal,
) -> None:
    plan, store = prepared_store(tmp_path, long_signal)
    backend = FlattenBackend()
    manager = OrderManager(FlattenExecutor(backend), store)

    first = manager.flatten_managed_positions()
    second = manager.flatten_managed_positions()
    assert first[0]["state"] == "SUBMITTED"
    assert second[0]["reason"] == "FLATTEN_RECONCILIATION_REQUIRED"
    assert backend.exit_calls == 1
    assert len(store.flatten_intents()) == 1

    intent = store.flatten_intent(plan.plan_id)
    backend.remote_exit = {
        "ordId": "exit-1", "clOrdId": intent["client_order_id"], "state": "filled",
        "accFillSz": str(plan.position_size), "avgPx": str(plan.entry + 1),
        "fillTime": str(now_ms()),
    }
    result = manager.flatten_managed_positions()[0]
    assert result["state"] == "FILLED"
    assert backend.exit_calls == 1
    assert store.managed_positions() == []
    assert backend.cancelled_protection == ["sl-1", "tp-1"]
    store.close()


def test_flatten_submission_unknown_is_reconciled_without_blind_retry(
    tmp_path, long_signal,
) -> None:
    _plan, store = prepared_store(tmp_path, long_signal)
    backend = FlattenBackend()
    manager = OrderManager(FlattenExecutor(backend, uncertain=True), store)
    first = manager.flatten_managed_positions()[0]
    second = manager.flatten_managed_positions()[0]
    assert first["state"] == "SUBMISSION_UNKNOWN"
    assert second["state"] == "SUBMISSION_UNKNOWN"
    assert backend.exit_calls == 1
    assert len(store.managed_positions()) == 1
    store.close()


def test_partial_flatten_is_reconciled_without_a_second_close_order(
    tmp_path, long_signal,
) -> None:
    plan, store = prepared_store(tmp_path, long_signal)
    backend = FlattenBackend()
    manager = OrderManager(FlattenExecutor(backend), store)
    manager.flatten_managed_positions()
    intent = store.flatten_intent(plan.plan_id)
    backend.remote_exit = {
        "ordId": "exit-1", "clOrdId": intent["client_order_id"],
        "state": "partially_filled", "accFillSz": str(plan.position_size / 2),
        "avgPx": str(plan.entry + 1), "fillTime": str(now_ms()),
    }

    partial = manager.flatten_managed_positions()[0]
    assert partial["state"] == OrderState.PARTIALLY_FILLED.value
    assert partial["filled_quantity"] == plan.position_size / 2
    assert backend.exit_calls == 1
    assert len(store.managed_positions()) == 1
    assert backend.cancelled_protection == []

    backend.remote_exit["state"] = "filled"
    backend.remote_exit["accFillSz"] = str(plan.position_size)
    filled = manager.flatten_managed_positions()[0]
    assert filled["state"] == "FILLED"
    assert backend.exit_calls == 1
    assert store.managed_positions() == []
    assert backend.cancelled_protection == ["sl-1", "tp-1"]
    store.close()
