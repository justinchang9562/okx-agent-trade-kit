from __future__ import annotations

import os
from typing import Any

from decision.trade_plan import TradePlan
from execution.base_backend import BaseBackend


class LiveExecutor:
    def __init__(self, backend: BaseBackend) -> None:
        self.backend = backend

    def execute(self, plan: TradePlan, live_flag: bool, confirmation: str) -> dict[str, Any]:
        gates = (
            plan.environment == "live",
            os.environ.get("LIVE_TRADING_ENABLED", "false").lower() == "true",
            live_flag,
            confirmation == "CONFIRM LIVE TRADING",
        )
        if not all(gates):
            raise RuntimeError("LIVE_TRADING_LOCKED")
        raise RuntimeError("LIVE_TRADING_NOT_IMPLEMENTED")
