from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
from typing import Any

from data.account_data import AccountDataService
from data.market_data import MarketDataService
from data.models import AccountSnapshot, MarketSnapshot
from decision.decision_engine import DecisionEngine
from decision.trade_plan import TradePlan
from execution.demo_executor import DemoExecutor
from execution.okx_adapter import OKXAdapter
from execution.order_manager import EXPLICIT_APPROVALS, OrderManager
from execution.order_state import OrderState
from monitoring.health import run_health
from monitoring.logger import configure_logging, log_event
from risk.exposure import ExposureSnapshot, build_exposure_snapshot
from risk.position_sizing import SizingResult, calculate_position_size
from risk.risk_manager import RiskDecision, RiskManager
from storage.signal_store import SignalStore
from storage.trade_store import TradeStore
from strategies.scalping_strategy import ScalpingStrategy
from trading_agent.config import AppConfig
from trading_agent.state import AgentState, RuntimeMode


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _ticker_price(payload: dict[str, Any]) -> float | None:
    value: Any = payload.get("data", payload)
    if isinstance(value, dict) and isinstance(value.get("data"), list):
        value = value["data"]
    if isinstance(value, list) and value and isinstance(value[0], dict):
        text = value[0].get("last")
        return float(text) if text not in (None, "") else None
    return None


class TradingOrchestrator:
    def __init__(self, config: AppConfig, adapter: OKXAdapter | None = None) -> None:
        self.config = config
        self.adapter = adapter or OKXAdapter(config.backend)
        self.market_data = MarketDataService(self.adapter.backend, config.rules)
        self.account_data = AccountDataService(self.adapter.backend)
        self.strategy = ScalpingStrategy(config.rules)
        self.risk = RiskManager(config.rules)
        self.decision = DecisionEngine()
        database = config.root / "trading_agent.db"
        self.trade_store = TradeStore(database)
        self.signal_store = SignalStore(database)
        self.order_manager = OrderManager(DemoExecutor(self.adapter.backend), self.trade_store)
        runtime = config.rules.get("runtime", {})
        self.state = AgentState(
            environment=config.environment, backend=config.backend,
            runtime_mode=RuntimeMode(runtime.get("default_state", "MANUAL_APPROVAL")),
            auto_demo_enabled=bool(runtime.get("auto_demo_enabled", False)),
        )
        self.logger = configure_logging(config.root / "logs" / "trading_agent.log")

    def _wallet_prices(self, current_market: MarketSnapshot, account: AccountSnapshot) -> dict[str, float]:
        prices = {current_market.instrument.base_currency: current_market.price}
        stable = {"USDT", "USDC", "USD"}
        candidates = set(self.config.symbols)
        candidates.update(
            f"{balance.currency}-USDT" for balance in account.balances
            if balance.currency not in stable and balance.equity > 0
        )
        for symbol in sorted(candidates):
            base = symbol.split("-")[0].upper()
            if base in prices:
                continue
            try:
                price = _ticker_price(self.adapter.backend.get_ticker(symbol))
            except Exception:
                price = None
            if price is not None and price > 0:
                prices[base] = price
        return prices

    def _exposure(
        self, account: AccountSnapshot, market: MarketSnapshot, proposed_notional: float = 0.0
    ) -> ExposureSnapshot:
        managed_notional = sum(
            position.quantity * position.entry_price
            for position in self.trade_store.managed_positions()
            if position.entry_price is not None
        )
        return build_exposure_snapshot(
            account, self._wallet_prices(market, account), managed_notional, proposed_notional
        )

    def _risk_and_sizing(
        self, market: MarketSnapshot, account: AccountSnapshot, duplicate: bool = False
    ) -> tuple[Any, RiskDecision, SizingResult, ExposureSnapshot]:
        signal = self.strategy.analyze(market)
        state = self.trade_store.daily_state()
        preliminary = self.risk.evaluate(signal, market, account, state, duplicate=duplicate)
        sizing = SizingResult(False, "SIGNAL_OR_RISK_REJECTED")
        if preliminary.approved and signal.suggested_stop is not None:
            sizing = calculate_position_size(
                account, market.instrument, signal.entry_price, signal.suggested_stop,
                float(self.config.rules["risk"]["risk_per_trade"]),
                float(self.config.rules["risk"]["max_position_pct"]),
            )
        exposure = self._exposure(account, market, sizing.notional_usdt if sizing.approved else 0.0)
        final = preliminary
        if preliminary.approved:
            final = self.risk.evaluate(signal, market, account, state, duplicate=duplicate, exposure=exposure)
            if not final.approved:
                sizing = SizingResult(False, final.reason)
        return signal, final, sizing, exposure

    def analyze(self, symbol: str, persist: bool = True) -> TradePlan:
        symbol = symbol.upper()
        if symbol not in self.config.symbols:
            raise ValueError(f"SYMBOL_NOT_CONFIGURED:{symbol}")
        market = self.market_data.get_snapshot(symbol)
        account = self.account_data.get_snapshot(symbol)
        signal, risk_decision, sizing, exposure = self._risk_and_sizing(market, account)
        plan = self.decision.build_plan(
            signal, risk_decision, sizing, self.config.environment, self.config.backend,
            account.equity_usdt, int(self.config.rules["execution"]["trade_plan_ttl_seconds"]),
        )
        if persist:
            self.signal_store.record(signal, plan)
            self.trade_store.save_plan(plan)
        payload = plan.as_dict() | {
            "managed_open_positions": self.trade_store.managed_open_count(),
            "wallet_exposure": asdict(exposure),
        }
        log_event(self.logger, "trade_plan", payload)
        return plan

    def approve_plan(self, plan_id: str, approval_text: str = "") -> dict[str, Any]:
        plan = self.trade_store.get_plan(plan_id)
        if plan is None:
            return {"status": "REJECTED", "reason": "PLAN_NOT_FOUND", "plan_id": plan_id}
        if plan.status != OrderState.PLANNED.value:
            return {"status": "REJECTED", "reason": "PLAN_NOT_PENDING", "plan_id": plan_id}
        current_ms = _now_ms()
        if plan.is_expired(current_ms):
            self.trade_store.reject_plan(plan_id, "PLAN_EXPIRED")
            return {"status": "REJECTED", "reason": "PLAN_EXPIRED", "plan_id": plan_id}
        market = self.market_data.get_snapshot(plan.symbol)
        account = self.account_data.get_snapshot(plan.symbol)
        executable_price = market.ask
        deviation_pct = abs(executable_price - plan.entry) / plan.entry * 100 if plan.entry > 0 else float("inf")
        max_deviation = float(self.config.rules["execution"]["max_entry_deviation_pct"])
        if deviation_pct > max_deviation:
            self.trade_store.reject_plan(plan_id, "PRICE_MOVED_TOO_FAR")
            return {"status": "REJECTED", "reason": "PRICE_MOVED_TOO_FAR", "plan_id": plan_id,
                    "entry_deviation_pct": deviation_pct, "limit_pct": max_deviation}
        fresh_signal, risk_decision, sizing, exposure = self._risk_and_sizing(
            market, account, duplicate=self.trade_store.is_duplicate(plan_id)
        )
        if not risk_decision.approved or not sizing.approved or fresh_signal.side != "LONG":
            reason = risk_decision.reason if not risk_decision.approved else sizing.reason
            if fresh_signal.side != "LONG":
                reason = "SIGNAL_NO_LONGER_VALID"
            self.trade_store.reject_plan(plan_id, reason)
            return {"status": "REJECTED", "reason": reason, "plan_id": plan_id}
        pre_submit_slippage_pct = abs(executable_price - market.price) / market.price * 100
        max_slippage = float(self.config.rules["execution"]["max_slippage_pct"])
        if pre_submit_slippage_pct > max_slippage:
            self.trade_store.reject_plan(plan_id, "PRE_SUBMIT_SLIPPAGE_TOO_HIGH")
            return {"status": "REJECTED", "reason": "PRE_SUBMIT_SLIPPAGE_TOO_HIGH", "plan_id": plan_id}
        final_plan = replace(
            plan, current_price=market.price, entry=executable_price,
            stop=fresh_signal.suggested_stop, take_profit=fresh_signal.suggested_take_profit,
            position_size=sizing.quantity, estimated_usdt=sizing.notional_usdt,
            risk_amount=sizing.risk_amount,
            risk_pct=sizing.risk_amount / account.equity_usdt if account.equity_usdt else 0.0,
            risk_reward=fresh_signal.risk_reward, signal_score=fresh_signal.score,
            signal_strength=fresh_signal.signal_strength, reasons=fresh_signal.reasons,
            risk_status="PASS", risk_approved=True, decision="BUY",
        )
        preview = {
            "status": "READY_FOR_EXACT_APPROVAL", "plan_id": plan_id, "symbol": plan.symbol,
            "planned_entry": plan.entry, "current_executable_price": executable_price,
            "entry_deviation_pct": deviation_pct, "position_size": sizing.quantity,
            "estimated_usdt": sizing.notional_usdt, "wallet_exposure": asdict(exposure),
            "managed_open_positions": self.trade_store.managed_open_count(),
            "expires_at_ms": plan.expires_at_ms,
        }
        if approval_text.strip() not in EXPLICIT_APPROVALS:
            return preview | {"execution": "BLOCKED_EXPLICIT_APPROVAL_REQUIRED"}
        if not self.trade_store.update_pending_plan_snapshot(final_plan):
            return {"status": "REJECTED", "reason": "PLAN_NOT_PENDING", "plan_id": plan_id}
        result = self.order_manager.submit(final_plan, approval_text, expected_price=executable_price)
        return preview | {"status": result.get("state", "SUBMITTED"), "execution": result}

    def reject_plan(self, plan_id: str) -> dict[str, Any]:
        rejected = self.trade_store.reject_plan(plan_id, "USER_REJECTED")
        return {"plan_id": plan_id, "status": "REJECTED" if rejected else "NOT_PENDING"}

    def get_pending_plans(self) -> list[dict[str, Any]]:
        return self.trade_store.list_pending_plans()

    def recover(self) -> list[dict[str, Any]]:
        return self.order_manager.recover_active_orders()

    def scan(self) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for symbol in self.config.symbols:
            try:
                output[symbol] = self.analyze(symbol).as_dict()
            except Exception as exc:
                output[symbol] = {"decision": "REJECT", "status": "DATA_UNAVAILABLE", "reason": str(exc)}
        return output

    def positions(self) -> dict[str, Any]:
        account = self.account_data.get_snapshot()
        try:
            reference_market = self.market_data.get_snapshot(self.config.symbols[0])
            exposure: dict[str, Any] | str = asdict(self._exposure(account, reference_market))
        except Exception:
            exposure = "DATA_UNAVAILABLE"
        return {
            "wallet_balances": {balance.currency: balance.equity for balance in account.balances if balance.equity != 0},
            "managed_positions": [asdict(item) for item in self.trade_store.managed_positions()],
            "managed_open_positions": self.trade_store.managed_open_count(),
            "account_exposure": exposure,
        }

    def orders(self) -> dict[str, Any]:
        account = self.account_data.get_snapshot()
        return {
            "okx_open_orders": [asdict(order) for order in account.open_orders],
            "agent_order_lifecycle": self.trade_store.get_orders(),
        }

    def get_trades(self) -> list[dict[str, Any]]:
        return self.trade_store.get_trades()

    def get_status(self) -> dict[str, Any]:
        return {
            "environment": self.config.environment, "backend": self.config.backend,
            "backend_status": asdict(self.adapter.backend.status()),
            "runtime_mode": self.state.runtime_mode.value,
            "auto_demo": "DISABLED" if not self.state.auto_demo_enabled else "CONFIGURED",
            "managed_open_positions": self.trade_store.managed_open_count(),
            "pending_plans": len(self.trade_store.list_pending_plans()),
            "live": "LOCKED",
        }

    def get_health(self) -> dict[str, Any]:
        return run_health(self.config, self.adapter)

    def stop_trading(self) -> dict[str, str]:
        self.state.set_mode(RuntimeMode.STOPPED)
        return {"runtime_mode": self.state.runtime_mode.value}

    def close(self) -> None:
        self.trade_store.close()
        self.signal_store.close()
        self.adapter.close()

    def __enter__(self) -> "TradingOrchestrator":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
