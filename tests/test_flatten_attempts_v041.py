from __future__ import annotations

from execution.errors import SubmissionUncertainError
from execution.order_manager import OrderManager
from execution.targeted_reconciler import TargetedOrderReconciler
from tests.test_session_flatten import prepared_store


class AttemptBackend:
    def __init__(self) -> None:
        self.orders: dict[str, dict] = {}
        self.exit_requests: list[dict] = []
        self.cleanup_failures = 0
        self.cleanup_calls: list[str] = []
        self.cancel_requests: list[str] = []

    def get_order_by_client_id(self, _symbol, client_order_id):
        row = self.orders.get(client_order_id)
        return {"data": {"data": [row] if row else []}}

    def get_order(self, _symbol, order_id):
        row = next((item for item in self.orders.values() if item.get("ordId") == order_id), None)
        return {"data": {"data": [row] if row else []}}

    def cancel_order(self, _symbol, order_id):
        self.cancel_requests.append(order_id)
        row = next((item for item in self.orders.values() if item.get("ordId") == order_id), None)
        if row is not None:
            row["state"] = "canceled"
        return {"data": {"data": [{"sCode": "0", "ordId": order_id}]}}

    def cancel_protection_order(self, _symbol, order_id):
        self.cleanup_calls.append(order_id)
        if self.cleanup_failures:
            self.cleanup_failures -= 1
            raise RuntimeError("cleanup unavailable")
        return {"data": {"data": [{"sCode": "0"}]}}


class AttemptExecutor:
    def __init__(self, backend: AttemptBackend) -> None:
        self.backend = backend
        self.unknown = False
        self.reject_next = False

    def execute_managed_exit(self, symbol: str, quantity: float, client_order_id: str):
        number = len(self.backend.exit_requests) + 1
        request = {
            "symbol": symbol, "quantity": quantity, "client_order_id": client_order_id,
            "order_id": f"exit-{number}",
        }
        self.backend.exit_requests.append(request)
        if self.unknown:
            raise SubmissionUncertainError("MCP_TIMEOUT")
        if self.reject_next:
            self.reject_next = False
            return {"data": {"data": [{"sCode": "51000", "sMsg": "rejected fixture"}]}}
        return {"data": {"data": [{
            "sCode": "0", "ordId": request["order_id"], "clOrdId": client_order_id,
        }]}}


def manager(store, backend: AttemptBackend, executor: AttemptExecutor | None = None):
    return OrderManager(
        executor or AttemptExecutor(backend),
        store,
        TargetedOrderReconciler((0.0, 0.0), lambda _delay: None),
    )


def remote(request: dict, state: str, filled: float, price: float) -> dict:
    return {
        "ordId": request["order_id"],
        "clOrdId": request["client_order_id"],
        "state": state,
        "accFillSz": str(filled),
        "avgPx": str(price) if filled else "",
        "fillTime": "1800000000000",
    }


def test_partial_then_cancelled_creates_unique_remaining_attempt(tmp_path, long_signal) -> None:
    plan, store = prepared_store(tmp_path, long_signal)
    backend = AttemptBackend()
    order_manager = manager(store, backend)
    order_manager.flatten_managed_positions()
    first = backend.exit_requests[0]
    half = plan.position_size / 2
    backend.orders[first["client_order_id"]] = remote(first, "canceled", half, plan.entry + 1)

    order_manager.flatten_managed_positions()

    assert len(backend.exit_requests) == 2
    second = backend.exit_requests[1]
    assert second["quantity"] == half
    assert second["client_order_id"] != first["client_order_id"]
    assert [item["attempt_number"] for item in store.flatten_attempts_for_plan(plan.plan_id)] == [1, 2]
    assert sum(item["quantity"] for item in store.reconciled_exit_fills(plan.plan_id)) == half
    store.close()


def test_nonterminal_or_submission_unknown_never_creates_second_attempt(
    tmp_path, long_signal,
) -> None:
    plan, store = prepared_store(tmp_path, long_signal)
    backend = AttemptBackend()
    executor = AttemptExecutor(backend)
    executor.unknown = True
    order_manager = manager(store, backend, executor)
    first = order_manager.flatten_managed_positions()[0]
    second = order_manager.flatten_managed_positions()[0]
    assert first["state"] == "SUBMISSION_UNKNOWN"
    assert second["state"] == "SUBMISSION_UNKNOWN"
    assert len(backend.exit_requests) == 1
    assert len(store.flatten_attempts_for_plan(plan.plan_id)) == 1
    store.close()


def test_terminal_confirmed_rejection_allows_one_new_attempt(tmp_path, long_signal) -> None:
    plan, store = prepared_store(tmp_path, long_signal)
    backend = AttemptBackend()
    executor = AttemptExecutor(backend)
    executor.reject_next = True
    order_manager = manager(store, backend, executor)

    first = order_manager.flatten_managed_positions()[0]
    assert first["state"] == "REJECTED"
    assert store.flatten_attempt(plan.plan_id, 1)["terminal_confirmed"] == 1
    assert store.flatten_attempt(plan.plan_id, 1)["reconciliation_complete"] == 1

    second = order_manager.flatten_managed_positions()[-1]
    assert second["attempt_number"] == 2
    assert len(backend.exit_requests) == 2
    assert backend.exit_requests[1]["quantity"] == plan.position_size
    store.close()


def test_attempt_two_partial_then_full_closes_without_oversell(tmp_path, long_signal) -> None:
    plan, store = prepared_store(tmp_path, long_signal)
    backend = AttemptBackend()
    order_manager = manager(store, backend)
    order_manager.flatten_managed_positions()
    first = backend.exit_requests[0]
    half = plan.position_size / 2
    backend.orders[first["client_order_id"]] = remote(first, "canceled", half, plan.entry + 1)
    order_manager.flatten_managed_positions()
    second = backend.exit_requests[1]
    backend.orders[second["client_order_id"]] = remote(second, "partially_filled", half / 2, plan.entry + 2)
    order_manager.flatten_managed_positions()
    assert len(backend.exit_requests) == 2

    backend.orders[second["client_order_id"]] = remote(second, "filled", half, plan.entry + 2)
    result = order_manager.flatten_managed_positions()[-1]
    assert result["state"] == "FILLED"
    assert store.managed_positions() == []
    assert sum(item["quantity"] for item in store.reconciled_exit_fills(plan.plan_id)) == plan.position_size
    assert len(backend.exit_requests) == 2
    store.close()


def test_duplicate_aggregate_fill_updates_one_persisted_fact(tmp_path, long_signal) -> None:
    plan, store = prepared_store(tmp_path, long_signal)
    backend = AttemptBackend()
    order_manager = manager(store, backend)
    order_manager.flatten_managed_positions()
    request = backend.exit_requests[0]
    half = plan.position_size / 2
    backend.orders[request["client_order_id"]] = remote(request, "partially_filled", half, plan.entry + 1)
    order_manager.reconcile_flatten_intent(plan.plan_id, 1)
    order_manager.reconcile_flatten_intent(plan.plan_id, 1)
    fills = store.reconciled_exit_fills(plan.plan_id)
    assert len(fills) == 1
    assert fills[0]["quantity"] == half
    store.close()


def test_late_fill_on_old_attempt_cancels_new_attempt_before_it_can_oversell(
    tmp_path, long_signal,
) -> None:
    plan, store = prepared_store(tmp_path, long_signal)
    backend = AttemptBackend()
    order_manager = manager(store, backend)
    order_manager.flatten_managed_positions()
    first = backend.exit_requests[0]
    half = plan.position_size / 2
    backend.orders[first["client_order_id"]] = remote(first, "canceled", half, plan.entry + 1)
    order_manager.flatten_managed_positions()
    second = backend.exit_requests[1]
    backend.orders[second["client_order_id"]] = remote(second, "live", 0, plan.entry + 2)

    backend.orders[first["client_order_id"]] = remote(
        first, "filled", plan.position_size, plan.entry + 1,
    )
    result = order_manager.flatten_managed_positions()[-1]

    assert result["state"] == "CANCELLED"
    assert backend.cancel_requests == [second["order_id"]]
    assert len(backend.exit_requests) == 2
    assert store.managed_positions() == []
    assert sum(item["quantity"] for item in store.reconciled_exit_fills(plan.plan_id)) == plan.position_size
    store.close()


def test_protection_cleanup_failure_is_persisted_and_retried_without_new_close(
    tmp_path, long_signal,
) -> None:
    plan, store = prepared_store(tmp_path, long_signal)
    backend = AttemptBackend()
    backend.cleanup_failures = 1
    order_manager = manager(store, backend)
    order_manager.flatten_managed_positions()
    request = backend.exit_requests[0]
    backend.orders[request["client_order_id"]] = remote(
        request, "filled", plan.position_size, plan.entry + 1,
    )
    first = order_manager.flatten_managed_positions()[-1]
    assert first["state"] == "PROTECTION_CLEANUP_INCOMPLETE"
    assert store.managed_positions() == []
    assert len(backend.exit_requests) == 1

    second = order_manager.flatten_managed_positions()[0]
    assert second["state"] == "FILLED"
    assert len(backend.exit_requests) == 1
    assert backend.cleanup_calls == ["sl-1", "tp-1", "sl-1"]
    assert store.flatten_intent(plan.plan_id)["state"] == "FILLED"
    store.close()
