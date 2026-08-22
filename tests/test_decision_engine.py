from risk.position_sizing import SizingResult
from risk.risk_manager import RiskDecision
from decision.decision_engine import DecisionEngine


def test_decision_buy_and_reject(long_signal) -> None:
    sizing = SizingResult(True, "PASS", 1, 100, 2)
    buy = DecisionEngine().build_plan(long_signal, RiskDecision(True, "PASS"), sizing, "demo", "mcp", 1000)
    assert buy.decision == "BUY" and buy.risk_approved
    rejected = DecisionEngine().build_plan(long_signal, RiskDecision(False, "SPREAD_TOO_WIDE"), sizing, "demo", "mcp", 1000)
    assert rejected.decision == "REJECT" and not rejected.risk_approved
