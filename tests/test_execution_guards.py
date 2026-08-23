from dataclasses import replace

import pytest

from execution.base_backend import BackendStatus
from execution.demo_executor import DemoExecutor
from execution.live_executor import LiveExecutor


class Backend:
    def __init__(self, demo=True):
        self.demo = demo
        self.last_order = None
    def status(self): return BackendStatus("test", "CONNECTED", True, self.demo)
    def capabilities(self): return {"attached_tp_sl": True}
    def place_order(self, order):
        self.last_order = order
        return {"ok": True}


def test_demo_guard_blocks_non_demo_plan(long_signal) -> None:
    from decision.decision_engine import DecisionEngine
    from risk.position_sizing import SizingResult
    from risk.risk_manager import RiskDecision
    plan = DecisionEngine().build_plan(long_signal, RiskDecision(True, "PASS"), SizingResult(True, "PASS", 1, 100, 2), "live", "mcp", 1000)
    with pytest.raises(RuntimeError, match="DEMO_EXECUTION_GUARD_BLOCKED"):
        DemoExecutor(Backend()).execute(plan)


def test_live_is_locked_by_default(long_signal, monkeypatch) -> None:
    from decision.decision_engine import DecisionEngine
    from risk.position_sizing import SizingResult
    from risk.risk_manager import RiskDecision
    monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)
    plan = DecisionEngine().build_plan(long_signal, RiskDecision(True, "PASS"), SizingResult(True, "PASS", 1, 100, 2), "live", "native_api", 1000)
    with pytest.raises(RuntimeError, match="LIVE_TRADING_LOCKED"):
        LiveExecutor(Backend(False)).execute(plan, False, "")


def test_demo_order_numbers_never_use_exponent_notation(long_signal) -> None:
    from decision.decision_engine import DecisionEngine
    from risk.position_sizing import SizingResult
    from risk.risk_manager import RiskDecision

    plan = DecisionEngine().build_plan(
        long_signal,
        RiskDecision(True, "PASS"),
        SizingResult(True, "PASS", 0.00005, 3.85, 0.01),
        "demo",
        "mcp",
        1000,
    )
    plan = replace(plan, stop=1e-8, take_profit=5e-8)
    backend = Backend()

    DemoExecutor(backend).execute(plan)

    assert backend.last_order["sz"] == "0.00005"
    assert backend.last_order["slTriggerPx"] == "0.00000001"
    assert backend.last_order["tpTriggerPx"] == "0.00000005"


def test_managed_exit_quantity_never_uses_exponent_notation() -> None:
    backend = Backend()

    DemoExecutor(backend).execute_managed_exit("BTC-USDT", 1e-8, "close-1")

    assert backend.last_order["sz"] == "0.00000001"
