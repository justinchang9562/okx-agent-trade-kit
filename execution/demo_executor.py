from __future__ import annotations

from typing import Any

from decision.trade_plan import TradePlan
from execution.base_backend import BaseBackend
from execution.errors import PreSubmitRejectedError


class DemoExecutor:
    def __init__(self, backend: BaseBackend, entry_guard: Any | None = None) -> None:
        self.backend = backend
        self.entry_guard = entry_guard

    def _require_entry_allowed(self) -> None:
        if self.entry_guard is None:
            return
        try:
            self.entry_guard()
        except PermissionError as exc:
            raise PreSubmitRejectedError(str(exc)) from exc

    def execute(self, plan: TradePlan) -> dict[str, Any]:
        self._require_entry_allowed()
        status = self.backend.status()
        if plan.environment != "demo" or not status.available or not status.demo:
            raise PreSubmitRejectedError("DEMO_EXECUTION_GUARD_BLOCKED")
        if plan.decision != "BUY" or not plan.risk_approved:
            raise PreSubmitRejectedError("PLAN_NOT_EXECUTABLE")
        capabilities = self.backend.capabilities()
        if not capabilities.get("attached_tp_sl", False):
            raise PreSubmitRejectedError("TP_SL_BACKEND_NOT_SUPPORTED")
        self._require_entry_allowed()
        return self.backend.place_order({
            "instId": plan.symbol, "side": "buy", "ordType": "market",
            "sz": str(plan.position_size), "tdMode": "cash", "tgtCcy": "base_ccy",
            "clOrdId": plan.plan_id[:32],
            "slTriggerPx": str(plan.stop), "slOrdPx": "-1",
            "tpTriggerPx": str(plan.take_profit), "tpOrdPx": "-1",
        })

    def execute_managed_exit(
        self, symbol: str, quantity: float, client_order_id: str,
    ) -> dict[str, Any]:
        """Close persisted Agent-owned spot inventory; never used for wallet inventory."""
        status = self.backend.status()
        if not status.available or not status.demo:
            raise PreSubmitRejectedError("DEMO_EXECUTION_GUARD_BLOCKED")
        if quantity <= 0:
            raise PreSubmitRejectedError("INVALID_EXIT_QUANTITY")
        return self.backend.place_order({
            "instId": symbol,
            "side": "sell",
            "ordType": "market",
            "sz": str(quantity),
            "tdMode": "cash",
            "tgtCcy": "base_ccy",
            "clOrdId": client_order_id,
        })
