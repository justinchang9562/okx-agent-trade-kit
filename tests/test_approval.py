import pytest

from decision.decision_engine import DecisionEngine
from execution.order_manager import OrderManager
from risk.position_sizing import SizingResult
from risk.risk_manager import RiskDecision
from storage.trade_store import TradeStore


class Executor:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, plan):
        self.calls += 1
        return {"data": {"data": [{"ordId": "1", "sCode": "0"}]}}


def make_plan(long_signal):
    return DecisionEngine().build_plan(
        long_signal, RiskDecision(True, "PASS"), SizingResult(True, "PASS", 1, 100, 2),
        "demo", "mcp", 1000,
    )


def test_unconfirmed_plan_never_executes(long_signal, tmp_path) -> None:
    plan = make_plan(long_signal)
    store = TradeStore(tmp_path / "test.db")
    store.save_plan(plan)
    executor = Executor()
    with pytest.raises(PermissionError, match="EXPLICIT_USER_APPROVAL_REQUIRED"):
        OrderManager(executor, store).submit(plan, "OK")
    assert executor.calls == 0
    store.close()


def test_exact_approval_executes_once_and_persists_before_call(long_signal, tmp_path) -> None:
    plan = make_plan(long_signal)
    store = TradeStore(tmp_path / "test.db")
    store.save_plan(plan)
    executor = Executor()
    result = OrderManager(executor, store).submit(plan, "确认执行")
    assert result["state"] == "SUBMITTED"
    assert store.order_for_plan(plan.plan_id)["state"] == "SUBMITTED"
    assert executor.calls == 1
    with pytest.raises(RuntimeError, match="DUPLICATE_ORDER"):
        OrderManager(executor, store).submit(plan, "确认执行")
    assert executor.calls == 1
    store.close()
