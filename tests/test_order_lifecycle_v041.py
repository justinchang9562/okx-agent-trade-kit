from __future__ import annotations

from collections import deque

import pytest

from execution.errors import SubmissionUncertainError
from execution.order_manager import OrderManager
from execution.order_state import OrderState
from execution.targeted_reconciler import TargetedOrderReconciler
from storage.trade_store import TradeStore
from tests.test_core_hardening import plan_for


class RaceBackend:
    def __init__(self) -> None:
        self.lookups: deque[dict | None] = deque()
        self.protections: deque[list[dict]] = deque()
        self.cancel_mode = "ack"
        self.place_mode = "ack"
        self.place_calls = 0
        self.cancel_calls = 0

    def place_order(self, order):
        self.place_calls += 1
        if self.place_mode == "unknown":
            raise SubmissionUncertainError("MCP_TIMEOUT")
        return {"data": {"data": [{
            "sCode": "0", "ordId": "entry-1", "clOrdId": order["clOrdId"],
        }]}}

    def cancel_order(self, _symbol, _order_id):
        self.cancel_calls += 1
        if self.cancel_mode == "timeout":
            raise TimeoutError("cancel timeout")
        code = "51000" if self.cancel_mode == "reject" else "0"
        return {"data": {"data": [{"sCode": code}]}}

    def get_order_by_client_id(self, _symbol, _client_order_id):
        row = self._next(self.lookups)
        return {"data": {"data": [row] if row else []}}

    def get_order(self, _symbol, _order_id):
        return {"data": {"data": []}}

    def get_protection_orders(self, _symbol=None):
        rows = self._next(self.protections, default=[])
        return {"data": {"data": rows}}

    def get_instrument(self, _symbol):
        return {"data": {"data": [{"tickSz": "0.1"}]}}

    def capabilities(self):
        return {"attached_tp_sl": True}

    def get_fills(self, *_args, **_kwargs):
        return {"data": {"data": []}}

    @staticmethod
    def _next(values: deque, default=None):
        if not values:
            return default
        return values.popleft() if len(values) > 1 else values[0]


class RaceExecutor:
    def __init__(self, backend: RaceBackend) -> None:
        self.backend = backend

    def execute(self, plan):
        return self.backend.place_order({"clOrdId": plan.plan_id})


def manager_for(store: TradeStore, backend: RaceBackend) -> OrderManager:
    return OrderManager(
        RaceExecutor(backend),
        store,
        TargetedOrderReconciler((0.0, 0.0, 0.0, 0.0), lambda _delay: None),
    )


def valid_protection(plan, quantity: float | None = None) -> list[dict]:
    return [{
        "clOrdId": plan.plan_id,
        "algoId": "protection-1",
        "state": "live",
        "sz": str(quantity if quantity is not None else plan.position_size),
        "slTriggerPx": str(plan.stop),
        "tpTriggerPx": str(plan.take_profit),
    }]


def open_order(tmp_path, long_signal):
    store = TradeStore(tmp_path / "race.db")
    plan = plan_for(long_signal)
    store.save_plan(plan)
    store.approve_and_create_order(plan, plan.plan_id, plan.entry)
    store.transition_order(plan.plan_id, OrderState.SUBMITTED.value, okx_order_id="entry-1")
    store.transition_order(plan.plan_id, OrderState.OPEN.value)
    return plan, store


def row(plan, state: str, filled: float = 0.0) -> dict:
    return {
        "ordId": "entry-1", "clOrdId": plan.plan_id, "state": state,
        "accFillSz": str(filled), "avgPx": str(plan.entry) if filled else "",
    }


def test_cancel_ack_requires_remote_cancel_confirmation(tmp_path, long_signal) -> None:
    plan, store = open_order(tmp_path, long_signal)
    backend = RaceBackend()
    backend.lookups.append(row(plan, "canceled"))
    result = manager_for(store, backend).cancel_pending_entries()[0]
    assert result["state"] == OrderState.CANCELLED.value
    assert store.order_for_plan(plan.plan_id)["state"] == OrderState.CANCELLED.value
    store.close()


def test_manual_cancel_uses_same_cancel_requested_reconciliation_path(
    tmp_path, long_signal,
) -> None:
    plan, store = open_order(tmp_path, long_signal)
    backend = RaceBackend()
    backend.lookups.append(row(plan, "canceled"))
    result = manager_for(store, backend).cancel(plan.plan_id, "确认执行")
    assert result["state"] == OrderState.CANCELLED.value
    assert backend.cancel_calls == 1
    assert store.order_for_plan(plan.plan_id)["state"] == OrderState.CANCELLED.value
    store.close()


def test_cancel_fill_race_projects_managed_and_verifies_protection(tmp_path, long_signal) -> None:
    plan, store = open_order(tmp_path, long_signal)
    backend = RaceBackend()
    backend.lookups.append(row(plan, "filled", plan.position_size))
    backend.protections.append(valid_protection(plan))
    result = manager_for(store, backend).cancel_pending_entries()[0]
    assert result["state"] == OrderState.FILLED.value
    position = store.managed_positions()[0]
    assert position.quantity == plan.position_size
    assert position.protection_state == "PROTECTED"
    store.close()


def test_cancel_partial_then_terminal_cancel_preserves_partial_position(
    tmp_path, long_signal,
) -> None:
    plan, store = open_order(tmp_path, long_signal)
    backend = RaceBackend()
    quantity = plan.position_size / 2
    backend.lookups.extend([row(plan, "partially_filled", quantity), row(plan, "canceled", quantity)])
    backend.protections.extend([valid_protection(plan, quantity), valid_protection(plan, quantity)])
    result = manager_for(store, backend).cancel_pending_entries()[0]
    assert result["state"] == OrderState.CANCELLED.value
    position = store.managed_positions()[0]
    assert position.quantity == quantity
    assert position.protection_state == "PROTECTED"
    assert store.get_trades()[0]["quantity"] == quantity
    store.close()


def test_cancelled_partial_waits_for_delayed_protection(tmp_path, long_signal) -> None:
    plan, store = open_order(tmp_path, long_signal)
    backend = RaceBackend()
    quantity = plan.position_size / 2
    backend.lookups.append(row(plan, "canceled", quantity))
    backend.protections.extend([[], valid_protection(plan, quantity)])
    result = manager_for(store, backend).cancel_pending_entries()[0]
    assert result["state"] == OrderState.CANCELLED.value
    assert result["targeted_reconciliation_attempts"] == 2
    assert store.managed_positions()[0].protection_state == "PROTECTED"
    store.close()


def test_cancelled_partial_without_protection_is_explicitly_unprotected(
    tmp_path, long_signal,
) -> None:
    plan, store = open_order(tmp_path, long_signal)
    backend = RaceBackend()
    backend.lookups.append(row(plan, "canceled", plan.position_size / 2))
    backend.protections.append([])
    result = manager_for(store, backend).cancel_pending_entries()[0]
    assert result["state"] == OrderState.CANCELLED.value
    assert result["reason"] == "POSITION_UNPROTECTED"
    assert result["targeted_reconciliation_attempts"] == 4
    assert store.managed_positions()[0].protection_state == "PROTECTION_NOT_FOUND"
    store.close()


@pytest.mark.parametrize("mode", ["timeout", "reject"])
def test_cancel_timeout_or_rejection_never_claims_cancelled(tmp_path, long_signal, mode) -> None:
    plan, store = open_order(tmp_path, long_signal)
    backend = RaceBackend()
    backend.cancel_mode = mode
    result = manager_for(store, backend).cancel_pending_entries()[0]
    assert result["state"] == OrderState.OPEN.value
    assert store.order_for_plan(plan.plan_id)["state"] == OrderState.OPEN.value
    store.close()


def test_cancel_ack_with_unavailable_query_stays_cancel_requested(tmp_path, long_signal) -> None:
    plan, store = open_order(tmp_path, long_signal)
    backend = RaceBackend()
    result = manager_for(store, backend).cancel_pending_entries()[0]
    assert result["state"] == OrderState.CANCEL_REQUESTED.value
    assert store.order_for_plan(plan.plan_id)["state"] == OrderState.CANCEL_REQUESTED.value
    assert backend.cancel_calls == 1
    manager_for(store, backend).cancel_pending_entries()
    assert backend.cancel_calls == 1
    store.close()


def test_new_order_fast_lane_open_then_filled_and_protected(tmp_path, long_signal) -> None:
    store = TradeStore(tmp_path / "fast.db")
    plan = plan_for(long_signal)
    store.save_plan(plan)
    backend = RaceBackend()
    backend.lookups.extend([row(plan, "live"), row(plan, "filled", plan.position_size)])
    backend.protections.append(valid_protection(plan))
    result = manager_for(store, backend).submit_automatic(plan)
    assert result["state"] == OrderState.FILLED.value
    assert result["reconciliation"] == "COMPLETE"
    assert backend.place_calls == 1
    assert store.managed_positions()[0].protection_state == "PROTECTED"
    store.close()


def test_new_order_fast_lane_immediate_fill_is_protected(tmp_path, long_signal) -> None:
    store = TradeStore(tmp_path / "fast.db")
    plan = plan_for(long_signal)
    store.save_plan(plan)
    backend = RaceBackend()
    backend.lookups.append(row(plan, "filled", plan.position_size))
    backend.protections.append(valid_protection(plan))
    result = manager_for(store, backend).submit_automatic(plan)
    assert result["state"] == OrderState.FILLED.value
    assert result["targeted_reconciliation_attempts"] == 1
    assert backend.place_calls == 1
    store.close()


def test_new_order_fast_lane_partial_then_filled_revalidates_full_protection(
    tmp_path, long_signal,
) -> None:
    store = TradeStore(tmp_path / "fast.db")
    plan = plan_for(long_signal)
    store.save_plan(plan)
    backend = RaceBackend()
    half = plan.position_size / 2
    backend.lookups.extend([
        row(plan, "partially_filled", half),
        row(plan, "filled", plan.position_size),
    ])
    backend.protections.extend([
        valid_protection(plan, half),
        valid_protection(plan, plan.position_size),
    ])
    result = manager_for(store, backend).submit_automatic(plan)
    assert result["state"] == OrderState.FILLED.value
    assert result["targeted_reconciliation_attempts"] == 2
    assert store.managed_positions()[0].quantity == plan.position_size
    assert store.managed_positions()[0].protection_state == "PROTECTED"
    assert backend.place_calls == 1
    store.close()


def test_new_order_fast_lane_waits_for_delayed_protection(tmp_path, long_signal) -> None:
    store = TradeStore(tmp_path / "fast.db")
    plan = plan_for(long_signal)
    store.save_plan(plan)
    backend = RaceBackend()
    backend.lookups.append(row(plan, "filled", plan.position_size))
    backend.protections.extend([[], valid_protection(plan)])
    result = manager_for(store, backend).submit_automatic(plan)
    assert result["state"] == OrderState.FILLED.value
    assert store.managed_positions()[0].protection_state == "PROTECTED"
    store.close()


def test_new_order_without_protection_fails_closed(tmp_path, long_signal) -> None:
    store = TradeStore(tmp_path / "fast.db")
    plan = plan_for(long_signal)
    store.save_plan(plan)
    backend = RaceBackend()
    backend.lookups.append(row(plan, "filled", plan.position_size))
    backend.protections.append([])
    result = manager_for(store, backend).submit_automatic(plan)
    assert result["state"] == OrderState.POSITION_UNPROTECTED.value
    assert result["reconciliation"] == "POSITION_UNPROTECTED"
    assert backend.place_calls == 1
    store.close()


@pytest.mark.parametrize("found", [True, False])
def test_submission_unknown_never_blindly_resubmits(tmp_path, long_signal, found) -> None:
    store = TradeStore(tmp_path / "fast.db")
    plan = plan_for(long_signal)
    store.save_plan(plan)
    backend = RaceBackend()
    backend.place_mode = "unknown"
    if found:
        backend.lookups.append(row(plan, "live"))
        result = manager_for(store, backend).submit_automatic(plan)
        assert result["state"] == OrderState.OPEN.value
    else:
        with pytest.raises(RuntimeError, match="SUBMISSION_UNKNOWN"):
            manager_for(store, backend).submit_automatic(plan)
        assert store.order_for_plan(plan.plan_id)["state"] == OrderState.SUBMISSION_UNKNOWN.value
    assert backend.place_calls == 1
    store.close()


def test_fast_lane_timeout_is_safe_and_scheduler_later_recovers(
    tmp_path, long_signal,
) -> None:
    store = TradeStore(tmp_path / "fast.db")
    plan = plan_for(long_signal)
    store.save_plan(plan)
    backend = RaceBackend()
    order_manager = manager_for(store, backend)
    first = order_manager.submit_automatic(plan)
    assert first["state"] == OrderState.SUBMITTED.value
    assert first["reconciliation"] == "RECONCILIATION_REQUIRED"
    assert first["targeted_reconciliation_attempts"] == 4

    backend.lookups.append(row(plan, "filled", plan.position_size))
    backend.protections.append(valid_protection(plan))
    recovered = order_manager.recover_active_orders()
    assert recovered[0]["state"] == OrderState.FILLED.value
    assert store.managed_positions()[0].protection_state == "PROTECTED"
    assert backend.place_calls == 1
    store.close()


def test_default_targeted_reconciliation_schedule_is_exact_and_bounded() -> None:
    sleeps: list[float] = []
    lookups = 0

    def lookup() -> dict:
        nonlocal lookups
        lookups += 1
        return {"state": "FILLED" if lookups == 4 else "OPEN"}

    reconciler = TargetedOrderReconciler(sleeper=sleeps.append)
    result = reconciler.run(lookup, lambda item: item["state"] == "FILLED")
    assert reconciler.delays_seconds == (0.0, 0.3, 1.0, 2.0)
    assert sleeps == [0.3, 1.0, 2.0]
    assert lookups == 4
    assert result["targeted_reconciliation_attempts"] == 4
