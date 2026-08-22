from __future__ import annotations

from typing import Any

from decision.trade_plan import TradePlan
from execution.base_backend import BaseBackend


class DemoExecutor:
    def __init__(self, backend: BaseBackend) -> None:
        self.backend = backend

    def execute(self, plan: TradePlan) -> dict[str, Any]:
        status = self.backend.status()
        if plan.environment != "demo" or not status.available or not status.demo:
            raise RuntimeError("DEMO_EXECUTION_GUARD_BLOCKED")
        if plan.decision != "BUY" or not plan.risk_approved:
            raise RuntimeError("PLAN_NOT_EXECUTABLE")
        capabilities = self.backend.capabilities()
        if not capabilities.get("attached_tp_sl", False):
            raise RuntimeError("TP_SL_BACKEND_NOT_SUPPORTED")
        return self.backend.place_order({
            "instId": plan.symbol, "side": "buy", "ordType": "market",
            "sz": str(plan.position_size), "tdMode": "cash", "tgtCcy": "base_ccy",
            "clOrdId": plan.plan_id[:32],
            "slTriggerPx": str(plan.stop), "slOrdPx": "-1",
            "tpTriggerPx": str(plan.take_profit), "tpOrdPx": "-1",
        })
