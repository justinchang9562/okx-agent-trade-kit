from __future__ import annotations

import pytest

from execution.order_manager import OrderManager
from execution.order_state import OrderState
from storage.trade_store import TradeStore
from tests.test_core_hardening import Executor, LifecycleBackend, plan_for


def prepared(tmp_path, long_signal):
    backend = LifecycleBackend()
    store = TradeStore(tmp_path / "protection.db")
    plan = plan_for(long_signal)
    store.save_plan(plan)
    store.approve_and_create_order(plan, plan.plan_id, plan.entry)
    store.transition_order(plan.plan_id, OrderState.SUBMITTED.value, okx_order_id="okx-1")
    manager = OrderManager(Executor(backend), store)
    local = store.order_for_plan(plan.plan_id)
    return plan, backend, store, manager, local


def row(plan, **changes):
    value = {
        "algoClOrdId": plan.plan_id,
        "algoId": "algo-1",
        "state": "effective",
        "sz": str(plan.position_size),
        "slTriggerPx": str(plan.stop),
        "tpTriggerPx": str(plan.take_profit),
    }
    value.update(changes)
    return value


def test_separate_sl_and_tp_rows_are_fully_verified(tmp_path, long_signal) -> None:
    plan, backend, store, manager, local = prepared(tmp_path, long_signal)
    backend.protection = [
        row(plan, algoId="sl-1", tpTriggerPx=""),
        row(plan, algoId="tp-1", slTriggerPx=""),
    ]
    state, identifiers = manager._protection_state(local, "okx-1", plan.position_size)
    assert state == "PROTECTED"
    assert identifiers == ["sl-1", "tp-1"]
    store.close()


def test_one_valid_and_one_stale_duplicate_remains_protected(tmp_path, long_signal) -> None:
    plan, backend, store, manager, local = prepared(tmp_path, long_signal)
    backend.protection = [row(plan), row(plan, algoId="stale", state="cancelled")]
    assert manager._protection_state(local, "okx-1", plan.position_size)[0] == "PROTECTED"
    store.close()


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"state": "expired"}, "PROTECTION_NOT_ACTIVE"),
        ({"state": "failed"}, "PROTECTION_NOT_ACTIVE"),
        ({"slTriggerPx": "0"}, "STOP_LOSS_NOT_VERIFIED"),
        ({"tpTriggerPx": None}, "TAKE_PROFIT_NOT_VERIFIED"),
        ({"sz": "malformed"}, "PROTECTION_QUANTITY_MISMATCH"),
    ],
)
def test_invalid_algo_edges_never_report_protected(
    tmp_path, long_signal, changes, expected,
) -> None:
    plan, backend, store, manager, local = prepared(tmp_path, long_signal)
    backend.protection = [row(plan, **changes)]
    assert manager._protection_state(local, "okx-1", plan.position_size)[0] == expected
    store.close()


def test_partial_fill_growth_revalidates_coverage(tmp_path, long_signal) -> None:
    plan, backend, store, manager, local = prepared(tmp_path, long_signal)
    first_fill = plan.position_size * 0.4
    later_fill = plan.position_size * 0.8
    backend.protection = [row(plan, sz=str(first_fill))]
    assert manager._protection_state(local, "okx-1", first_fill)[0] == "PROTECTED"
    assert manager._protection_state(local, "okx-1", later_fill)[0] == "PROTECTION_QUANTITY_MISMATCH"
    store.close()


def test_attached_oco_without_parent_ids_matches_server_fingerprint_and_net_fill(
    tmp_path, long_signal,
) -> None:
    plan, backend, store, manager, local = prepared(tmp_path, long_signal)
    backend.get_instrument = lambda _symbol: {
        "data": {"data": [{"tickSz": "0.1", "lotSz": "0.00000001"}]},
    }
    fill_time = "1787554264554"
    filled = 0.99226344
    fee = -0.000793810752
    backend.protection = [{
        "algoId": "active-oco-1",
        "instId": plan.symbol,
        "ordType": "oco",
        "state": "live",
        "side": "sell",
        "cTime": fill_time,
        "sz": "0.99146962",
        "slTriggerPx": str(plan.stop),
        "tpTriggerPx": str(plan.take_profit),
    }]
    remote = {
        "ordId": "okx-1",
        "clOrdId": plan.plan_id,
        "state": "filled",
        "accFillSz": str(filled),
        "fee": str(fee),
        "feeCcy": "BTC",
        "fillTime": fill_time,
        "attachAlgoOrds": [{
            "attachAlgoId": "requested-attachment-1",
            "slTriggerPx": str(plan.stop),
            "tpTriggerPx": str(plan.take_profit),
            "failCode": "",
        }],
    }

    state, identifiers = manager._protection_state(
        local, "okx-1", filled, remote_order=remote,
    )

    assert state == "PROTECTED"
    assert identifiers == ["active-oco-1"]
    store.close()


@pytest.mark.parametrize("mutation", [
    {"cTime": "1787554270000"},
    {"side": "buy"},
    {"tpTriggerPx": "999999"},
])
def test_attached_oco_fingerprint_never_matches_unrelated_order(
    tmp_path, long_signal, mutation,
) -> None:
    plan, backend, store, manager, local = prepared(tmp_path, long_signal)
    candidate = {
        "algoId": "unrelated-oco",
        "instId": plan.symbol,
        "ordType": "oco",
        "state": "live",
        "side": "sell",
        "cTime": "1787554264554",
        "sz": str(plan.position_size),
        "slTriggerPx": str(plan.stop),
        "tpTriggerPx": str(plan.take_profit),
    }
    candidate.update(mutation)
    backend.protection = [candidate]
    remote = {
        "fillTime": "1787554264554",
        "attachAlgoOrds": [{
            "slTriggerPx": str(plan.stop),
            "tpTriggerPx": str(plan.take_profit),
            "failCode": "",
        }],
    }
    assert manager._protection_state(
        local, "okx-1", plan.position_size, remote_order=remote,
    )[0] == "PROTECTION_NOT_FOUND"
    store.close()
