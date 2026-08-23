from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import Any

from data.account_data import AccountDataService
from data.market_data import MarketDataService
from data.models import AccountSnapshot, MarketSnapshot
from data.realtime.snapshot_provider import MarketSnapshotProvider, PollingMarketSnapshotProvider
from decision.decision_engine import DecisionEngine
from decision.trade_plan import TradePlan
from execution.demo_executor import DemoExecutor
from execution.okx_adapter import OKXAdapter
from execution.order_manager import EXPLICIT_APPROVALS, OrderManager
from execution.order_state import OrderState
from monitoring.health import run_health
from monitoring.logger import configure_logging, log_event
from risk.daily_limits import daily_loss_reached
from risk.execution_revalidation import build_executable_long_plan
from risk.exposure import ExposureSnapshot, build_exposure_snapshot
from risk.position_sizing import SizingResult, calculate_position_size, cap_position_size
from risk.risk_manager import RiskDecision, RiskManager
from storage.signal_store import SignalStore
from storage.trade_store import TradeStore
from strategies.registry import PRODUCTION_STRATEGY_VERSION, build_strategy
from trading_agent.config import AppConfig
from trading_agent.state import AgentState, RuntimeMode


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _ticker_price(payload: dict[str, Any]) -> float | None:
    value: Any = payload.get("data", payload)
    if isinstance(value, dict) and isinstance(value.get("data"), list):
        value = value["data"]
    if isinstance(value, list) and value and isinstance(value[0], dict):
        text = value[0].get("last")
        return float(text) if text not in (None, "") else None
    return None


def _change_pct(current: Any, expected: Any) -> float:
    try:
        current_value = float(current)
        expected_value = float(expected)
    except (TypeError, ValueError):
        return float("inf")
    if expected_value == 0:
        return 0.0 if current_value == 0 else float("inf")
    return abs(current_value - expected_value) / abs(expected_value) * 100


def approval_preview_change(
    current: dict[str, Any], expected: dict[str, Any], rules: dict[str, Any],
) -> dict[str, float]:
    approval = rules.get("approval", {})
    limits = {
        "current_executable_price": float(approval.get("max_preview_price_change_pct", 0.15)),
        "final_stop": float(approval.get("max_preview_price_change_pct", 0.15)),
        "final_take_profit": float(approval.get("max_preview_price_change_pct", 0.15)),
        "position_size": float(approval.get("max_preview_size_change_pct", 1.0)),
        "final_risk_amount": float(approval.get("max_preview_risk_change_pct", 1.0)),
    }
    return {
        field: _change_pct(current.get(field), expected.get(field))
        for field, limit in limits.items()
        if _change_pct(current.get(field), expected.get(field)) > limit
    }


class TradingOrchestrator:
    def __init__(self, config: AppConfig, adapter: OKXAdapter | None = None) -> None:
        self.config = config
        self.adapter = adapter or OKXAdapter(config.backend)
        self.market_data = MarketDataService(self.adapter.backend, config.rules)
        self.market_provider: MarketSnapshotProvider = PollingMarketSnapshotProvider(self.market_data)
        self.account_data = AccountDataService(self.adapter.backend)
        strategy_version = str(config.rules.get("strategy_version", PRODUCTION_STRATEGY_VERSION))
        self.strategy = build_strategy(strategy_version, config.rules, production=True)
        self.risk = RiskManager(config.rules)
        self.decision = DecisionEngine()
        database = config.root / "trading_agent.db"
        self.trade_store = TradeStore(database)
        self.signal_store = SignalStore(database)
        runtime = config.rules.get("runtime", {})
        self.state = AgentState(
            environment=config.environment, backend=config.backend,
            runtime_mode=RuntimeMode(runtime.get("default_state", "MANUAL_APPROVAL")),
            auto_demo_enabled=bool(runtime.get("auto_demo_enabled", False)),
        )
        self.order_manager = OrderManager(
            DemoExecutor(self.adapter.backend, entry_guard=self.state.require_new_entry_allowed),
            self.trade_store,
        )
        self.logger = configure_logging(config.root / "logs" / "trading_agent.log")

    def set_market_provider(self, provider: MarketSnapshotProvider) -> None:
        self.market_provider = provider

    def _market_snapshot(self, symbol: str) -> MarketSnapshot:
        if isinstance(self.market_provider, PollingMarketSnapshotProvider):
            return self.market_data.get_snapshot(symbol)
        return self.market_provider.get_snapshot(symbol)

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
            account=account,
            prices=self._wallet_prices(market, account),
            managed_notional_usdt=managed_notional,
            reserved_notional_usdt=self.trade_store.reserved_entry_notional(),
            proposed_notional_usdt=proposed_notional,
            dust_quantity=float(self.config.rules["risk"].get("unpriced_asset_dust_quantity", 0.0)),
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
        market = self._market_snapshot(symbol)
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
            "position_slots_in_use": self.trade_store.position_slots_in_use(),
            "wallet_exposure": asdict(exposure),
        }
        log_event(self.logger, "trade_plan", payload)
        return plan

    def approve_plan(
        self,
        plan_id: str,
        approval_text: str = "",
        *,
        position_size_cap: float | None = None,
        expected_preview: dict[str, Any] | None = None,
        session_authorized: bool = False,
    ) -> dict[str, Any]:
        if not self.state.allows_new_entries:
            return {"status": "REJECTED", "reason": "TRADING_STOPPED", "plan_id": plan_id}
        plan = self.trade_store.get_plan(plan_id)
        if plan is None:
            return {"status": "REJECTED", "reason": "PLAN_NOT_FOUND", "plan_id": plan_id}
        if plan.status != OrderState.PLANNED.value:
            return {"status": "REJECTED", "reason": "PLAN_NOT_PENDING", "plan_id": plan_id}
        current_ms = _now_ms()
        if plan.is_expired(current_ms):
            self.trade_store.reject_plan(plan_id, "PLAN_EXPIRED")
            return {"status": "REJECTED", "reason": "PLAN_EXPIRED", "plan_id": plan_id}
        market = self._market_snapshot(plan.symbol)
        account = self.account_data.get_snapshot(plan.symbol)
        fresh_signal = self.strategy.analyze(market)
        if fresh_signal.side != "LONG":
            self.trade_store.reject_plan(plan_id, "SIGNAL_NO_LONGER_VALID")
            return {"status": "REJECTED", "reason": "SIGNAL_NO_LONGER_VALID", "plan_id": plan_id}
        state = self.trade_store.daily_state()
        duplicate = self.trade_store.is_duplicate(plan_id)
        preliminary = build_executable_long_plan(
            fresh_signal,
            market,
            account,
            state,
            self.config.rules,
            duplicate=duplicate,
            risk_manager=self.risk,
        )
        executable_price = preliminary.entry
        final_stop = preliminary.stop
        final_take_profit = preliminary.take_profit
        final_rr = preliminary.risk_reward
        final_signal = preliminary.signal
        deviation_pct = abs(executable_price - plan.entry) / plan.entry * 100 if plan.entry > 0 else float("inf")
        max_deviation = float(self.config.rules["execution"]["max_entry_deviation_pct"])
        if deviation_pct > max_deviation:
            self.trade_store.reject_plan(plan_id, "PRICE_MOVED_TOO_FAR")
            return {"status": "REJECTED", "reason": "PRICE_MOVED_TOO_FAR", "plan_id": plan_id,
                    "entry_deviation_pct": deviation_pct, "limit_pct": max_deviation}
        if not preliminary.approved:
            self.trade_store.reject_plan(plan_id, preliminary.reason)
            return {"status": "REJECTED", "reason": preliminary.reason, "plan_id": plan_id}
        exposure = self._exposure(account, market, preliminary.sizing.notional_usdt)
        final = build_executable_long_plan(
            fresh_signal,
            market,
            account,
            state,
            self.config.rules,
            duplicate=duplicate,
            exposure=exposure,
            risk_manager=self.risk,
        )
        if not final.approved:
            self.trade_store.reject_plan(plan_id, final.reason)
            return {"status": "REJECTED", "reason": final.reason, "plan_id": plan_id}
        sizing = final.sizing
        if position_size_cap is not None:
            sizing = cap_position_size(
                sizing,
                market.instrument,
                executable_price,
                final_stop,
                position_size_cap,
            )
            if not sizing.approved:
                self.trade_store.reject_plan(plan_id, sizing.reason)
                return {"status": "REJECTED", "reason": sizing.reason, "plan_id": plan_id}
            exposure = self._exposure(account, market, sizing.notional_usdt)
            capped_risk = self.risk.evaluate(
                final_signal,
                market,
                account,
                state,
                duplicate=duplicate,
                exposure=exposure,
            )
            if not capped_risk.approved:
                self.trade_store.reject_plan(plan_id, capped_risk.reason)
                return {"status": "REJECTED", "reason": capped_risk.reason, "plan_id": plan_id}
        pre_submit_slippage_pct = abs(executable_price - market.price) / market.price * 100
        max_slippage = float(self.config.rules["execution"]["max_slippage_pct"])
        if pre_submit_slippage_pct > max_slippage:
            self.trade_store.reject_plan(plan_id, "PRE_SUBMIT_SLIPPAGE_TOO_HIGH")
            return {"status": "REJECTED", "reason": "PRE_SUBMIT_SLIPPAGE_TOO_HIGH", "plan_id": plan_id}
        final_plan = replace(
            plan, current_price=market.price, entry=executable_price,
            stop=final_stop, take_profit=final_take_profit,
            position_size=sizing.quantity, estimated_usdt=sizing.notional_usdt,
            risk_amount=sizing.risk_amount,
            risk_pct=sizing.risk_amount / account.equity_usdt if account.equity_usdt else 0.0,
            risk_reward=final_rr, signal_score=final_signal.score,
            signal_strength=final_signal.signal_strength, reasons=final_signal.reasons,
            risk_status="PASS", risk_approved=True, decision="BUY",
        )
        preview = {
            "status": "READY_FOR_EXACT_APPROVAL", "plan_id": plan_id, "symbol": plan.symbol,
            "planned_entry": plan.entry, "current_executable_price": executable_price,
            "entry_deviation_pct": deviation_pct, "spread_pct": market.spread_pct,
            "previewed_at_ms": _now_ms(), "position_size": sizing.quantity,
            "final_stop": final_stop, "final_take_profit": final_take_profit,
            "final_risk_reward": final_rr, "final_risk_amount": sizing.risk_amount,
            "estimated_usdt": sizing.notional_usdt, "wallet_exposure": asdict(exposure),
            "managed_open_positions": self.trade_store.managed_open_count(),
            "position_slots_in_use": self.trade_store.position_slots_in_use(),
            "reserved_entry_notional": self.trade_store.reserved_entry_notional(),
            "expires_at_ms": plan.expires_at_ms,
        }
        if expected_preview is not None:
            changes = approval_preview_change(preview, expected_preview, self.config.rules)
            if changes:
                return {
                    "status": "REPREVIEW_REQUIRED",
                    "reason": "APPROVAL_PREVIEW_CHANGED",
                    "plan_id": plan_id,
                    "change_pct": changes,
                }
        if not session_authorized and approval_text.strip() not in EXPLICIT_APPROVALS:
            return preview | {"execution": "BLOCKED_EXPLICIT_APPROVAL_REQUIRED"}
        if not self.trade_store.update_pending_plan_snapshot(final_plan):
            return {"status": "REJECTED", "reason": "PLAN_NOT_PENDING", "plan_id": plan_id}
        result = (
            self.order_manager.submit_automatic(final_plan, expected_price=executable_price)
            if session_authorized
            else self.order_manager.submit(final_plan, approval_text, expected_price=executable_price)
        )
        return preview | {"status": result.get("state", "SUBMITTED"), "execution": result}

    def execute_plan_automatically(self, plan_id: str) -> dict[str, Any]:
        return self.approve_plan(plan_id, session_authorized=True)

    def reject_plan(self, plan_id: str) -> dict[str, Any]:
        rejected = self.trade_store.reject_plan(plan_id, "USER_REJECTED")
        return {"plan_id": plan_id, "status": "REJECTED" if rejected else "NOT_PENDING"}

    def get_pending_plans(self) -> list[dict[str, Any]]:
        return self.trade_store.list_pending_plans()

    def recover(self) -> list[dict[str, Any]]:
        return self.order_manager.recover_active_orders()

    def cancel_pending_entries(self) -> list[dict[str, Any]]:
        return self.order_manager.cancel_pending_entries()

    def flatten_managed_positions(self) -> list[dict[str, Any]]:
        return self.order_manager.flatten_managed_positions()

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
            reference_market = self._market_snapshot(self.config.symbols[0])
            exposure: dict[str, Any] | str = asdict(self._exposure(account, reference_market))
        except Exception:
            exposure = "DATA_UNAVAILABLE"
        return {
            "wallet_balances": {balance.currency: balance.equity for balance in account.balances if balance.equity != 0},
            "managed_positions": [asdict(item) for item in self.trade_store.managed_positions()],
            "managed_open_positions": self.trade_store.managed_open_count(),
            "position_slots_in_use": self.trade_store.position_slots_in_use(),
            "reserved_entry_notional": self.trade_store.reserved_entry_notional(),
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

    def get_trade_plans(self, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        return self.trade_store.list_plans(status=status, limit=limit)

    def get_signals(self, limit: int = 200) -> list[dict[str, Any]]:
        return self.signal_store.list_signals(limit=limit)

    def account(self) -> dict[str, Any]:
        account = self.account_data.get_snapshot()
        try:
            market = self._market_snapshot(self.config.symbols[0])
            exposure: dict[str, Any] | str = asdict(self._exposure(account, market))
        except Exception:
            exposure = "DATA_UNAVAILABLE"
        return {
            "timestamp_ms": account.timestamp_ms,
            "equity_usdt": account.equity_usdt,
            "available_usdt": account.available_usdt,
            "daily_pnl": account.daily_pnl,
            "wallet_balances": [asdict(balance) for balance in account.balances],
            "open_order_count": len(account.open_orders),
            "fill_count": len(account.fills),
            "exposure": exposure,
        }

    def synchronize_account(self) -> dict[str, Any]:
        """One account read fan-outs into dashboard projections and ownership tags."""
        account = self.account_data.get_snapshot()
        lifecycle = self.trade_store.get_orders()
        client_ids = {
            str(row.get("client_order_id")) for row in lifecycle if row.get("client_order_id")
        }
        order_ids = {str(row.get("okx_order_id")) for row in lifecycle if row.get("okx_order_id")}

        def origin(order_id: str, client_order_id: str = "") -> str:
            return "AGENT" if (
                bool(order_id) and order_id in order_ids
                or bool(client_order_id) and client_order_id in client_ids
            ) else "EXTERNAL"

        orders = [
            asdict(order) | {"origin": origin(order.order_id, order.client_order_id)}
            for order in account.open_orders
        ]
        fills = [asdict(fill) | {"origin": origin(fill.order_id)} for fill in account.fills]
        managed = self.trade_store.managed_positions()
        managed_by_currency: dict[str, float] = {}
        for position in managed:
            base = position.symbol.split("-")[0].upper()
            managed_by_currency[base] = managed_by_currency.get(base, 0.0) + max(
                0.0, position.quantity - position.exit_filled_quantity,
            )
        external_inventory = []
        for balance in account.balances:
            external = max(0.0, balance.equity - managed_by_currency.get(balance.currency, 0.0))
            if external > 0 and balance.currency not in {"USDT", "USDC", "USD"}:
                external_inventory.append({
                    "currency": balance.currency,
                    "quantity": external,
                    "origin": "EXTERNAL",
                    "managed": False,
                })
        try:
            market = self._market_snapshot(self.config.symbols[0])
            exposure: dict[str, Any] | str = asdict(self._exposure(account, market))
        except Exception:
            exposure = "DATA_UNAVAILABLE"
        return {
            "account": {
                "timestamp_ms": account.timestamp_ms,
                "equity_usdt": account.equity_usdt,
                "available_usdt": account.available_usdt,
                "daily_pnl": account.daily_pnl,
                "wallet_balances": [asdict(balance) for balance in account.balances],
                "open_order_count": len(orders),
                "fill_count": len(fills),
                "exposure": exposure,
            },
            "orders": {
                "okx_open_orders": orders,
                "agent_order_lifecycle": lifecycle,
            },
            "positions": {
                "managed_positions": [asdict(item) | {"origin": "AGENT"} for item in managed],
                "external_wallet_inventory": external_inventory,
                "managed_open_positions": len(managed),
                "position_slots_in_use": self.trade_store.position_slots_in_use(),
                "reserved_entry_notional": self.trade_store.reserved_entry_notional(),
                "account_exposure": exposure,
            },
            "fills": fills,
        }

    def get_status(self) -> dict[str, Any]:
        daily = self.trade_store.daily_state()
        return {
            "environment": self.config.environment, "backend": self.config.backend,
            "backend_status": asdict(self.adapter.backend.status()),
            "runtime_mode": self.state.runtime_mode.value,
            "auto_demo": "DISABLED" if not self.state.auto_demo_enabled else "CONFIGURED",
            "managed_open_positions": self.trade_store.managed_open_count(),
            "position_slots_in_use": self.trade_store.position_slots_in_use(),
            "reserved_entry_notional": self.trade_store.reserved_entry_notional(),
            "pending_plans": len(self.trade_store.list_pending_plans()),
            "daily_pnl": daily.realized_pnl,
            "consecutive_losses": daily.consecutive_losses,
            "live": "LOCKED",
        }

    def get_health(self) -> dict[str, Any]:
        health = run_health(self.config, self.adapter)
        blocking_reasons: list[str] = []
        details: dict[str, Any] = {}
        if health["system_capability"]["status"] != "READY":
            blocking_reasons.append("SYSTEM_CAPABILITY_NOT_READY")
        if self.state.kill_switch_active:
            blocking_reasons.append("KILL_SWITCH_ACTIVE")
        if not self.state.execution_armed:
            blocking_reasons.append("EXECUTION_DISARMED")
        if self.state.runtime_mode not in {RuntimeMode.MANUAL_APPROVAL, RuntimeMode.AUTO_DEMO}:
            blocking_reasons.append("TRADING_STOPPED")
        if any(order["state"] == OrderState.SUBMISSION_UNKNOWN.value for order in self.trade_store.get_orders()):
            blocking_reasons.append("SUBMISSION_UNKNOWN_REQUIRES_RECONCILIATION")
        if any(position.protection_state != "PROTECTED" for position in self.trade_store.managed_positions()):
            blocking_reasons.append("POSITION_UNPROTECTED")
        if health["system_capability"]["status"] == "READY":
            try:
                market = self._market_snapshot(self.config.symbols[0])
                account = self.account_data.get_snapshot(self.config.symbols[0])
                exposure = self._exposure(account, market)
                state = self.trade_store.daily_state()
                slots = self.trade_store.position_slots_in_use()
                details = {
                    "position_slots_in_use": slots,
                    "max_open_positions": int(self.config.rules["risk"]["max_open_positions"]),
                    "exposure": asdict(exposure),
                }
                if market.spread_pct > float(self.config.rules["scalping"]["max_spread_pct"]):
                    blocking_reasons.append("SPREAD_TOO_WIDE")
                if daily_loss_reached(
                    account.equity_usdt, state,
                    float(self.config.rules["risk"]["max_daily_loss_pct"]),
                ):
                    blocking_reasons.append("DAILY_KILL_SWITCH_ACTIVE")
                if state.consecutive_losses >= int(self.config.rules["risk"]["max_consecutive_losses"]):
                    blocking_reasons.append("MAX_CONSECUTIVE_LOSSES_REACHED")
                if slots >= int(self.config.rules["risk"]["max_open_positions"]):
                    blocking_reasons.append("MAX_OPEN_POSITIONS_REACHED")
                if exposure.status == "UNKNOWN":
                    blocking_reasons.append("EXPOSURE_UNKNOWN")
                if exposure.total_exposure_pct > float(self.config.rules["risk"]["max_total_exposure_pct"]):
                    blocking_reasons.append("MAX_TOTAL_EXPOSURE_REACHED")
            except Exception as exc:
                blocking_reasons.append("DATA_UNAVAILABLE")
                details = {"error_type": type(exc).__name__}
        blocking_reasons = list(dict.fromkeys(blocking_reasons))
        reason = blocking_reasons[0] if blocking_reasons else "ELIGIBLE"
        health["trading_eligibility"] = {
            "eligible": not blocking_reasons, "reason": reason,
            "blocking_reasons": blocking_reasons, **details,
        }
        health["live_trading"] = "LOCKED_NOT_IMPLEMENTED"
        return health

    def stop_trading(self) -> dict[str, str]:
        self.state.set_mode(RuntimeMode.STOPPED)
        return {"runtime_mode": self.state.runtime_mode.value}

    def close(self) -> None:
        self.trade_store.close()
        self.signal_store.close()
        self.adapter.close()

    def __enter__(self) -> TradingOrchestrator:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
