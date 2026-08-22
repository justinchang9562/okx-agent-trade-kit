from __future__ import annotations

from typing import Any

from trading_agent.orchestrator import TradingOrchestrator


class LocalControlAPI:
    """UI-safe facade. A future Web/macOS client never receives backend internals or credentials."""

    def __init__(self, orchestrator: TradingOrchestrator) -> None:
        self.agent = orchestrator

    def get_status(self) -> dict[str, Any]: return self.agent.get_status()
    def get_health(self) -> dict[str, Any]: return self.agent.get_health()
    def scan(self) -> dict[str, Any]: return self.agent.scan()
    def analyze(self, symbol: str) -> dict[str, Any]: return self.agent.analyze(symbol).as_dict()
    def get_pending_plans(self) -> list[dict[str, Any]]: return self.agent.get_pending_plans()
    def approve_plan(self, plan_id: str, approval_text: str = "") -> dict[str, Any]:
        return self.agent.approve_plan(plan_id, approval_text)
    def reject_plan(self, plan_id: str) -> dict[str, Any]: return self.agent.reject_plan(plan_id)
    def get_positions(self) -> dict[str, Any]: return self.agent.positions()
    def get_orders(self) -> dict[str, Any]: return self.agent.orders()
    def get_trades(self) -> list[dict[str, Any]]: return self.agent.get_trades()
    def stop_trading(self) -> dict[str, str]: return self.agent.stop_trading()
