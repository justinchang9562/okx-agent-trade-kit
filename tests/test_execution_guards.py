import pytest

from execution.base_backend import BackendStatus
from execution.demo_executor import DemoExecutor
from execution.live_executor import LiveExecutor


class Backend:
    def __init__(self, demo=True): self.demo = demo
    def status(self): return BackendStatus("test", "CONNECTED", True, self.demo)
    def place_order(self, order): return {"ok": True}


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
