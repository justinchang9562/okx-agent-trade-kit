from __future__ import annotations

from datetime import datetime, timezone

from decision.trade_plan import TradePlan
from execution.order_state import OrderState
from risk.position_sizing import SizingResult
from risk.risk_manager import RiskDecision
from strategies.signal import Signal


class DecisionEngine:
    def build_plan(
        self,
        signal: Signal,
        risk: RiskDecision,
        sizing: SizingResult,
        environment: str,
        backend: str,
        equity: float,
        ttl_seconds: int = 60,
    ) -> TradePlan:
        if signal.side == "BEARISH":
            decision = "HOLD"  # Spot bearish is not an implicit short.
        elif signal.side != "LONG":
            decision = "HOLD"
        elif not risk.approved or not sizing.approved:
            decision = "REJECT"
        else:
            decision = "BUY"
        risk_status = risk.reason if not risk.approved else sizing.reason
        created_at_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        return TradePlan(
            plan_id=TradePlan.identifier(signal.symbol, signal.side, signal.strategy, signal.timestamp_ms),
            symbol=signal.symbol, environment=environment, backend=backend, side=signal.side,
            current_price=signal.entry_price, entry=signal.entry_price, stop=signal.suggested_stop,
            take_profit=signal.suggested_take_profit, position_size=sizing.quantity,
            estimated_usdt=sizing.notional_usdt, risk_amount=sizing.risk_amount,
            risk_pct=(sizing.risk_amount / equity) if equity > 0 else 0.0,
            risk_reward=signal.risk_reward, signal_score=signal.score, signal_strength=signal.signal_strength,
            reasons=signal.reasons, risk_status=risk_status,
            risk_approved=risk.approved and sizing.approved, decision=decision,
            timestamp_ms=signal.timestamp_ms, strategy=signal.strategy,
            created_at_ms=created_at_ms, expires_at_ms=created_at_ms + ttl_seconds * 1000,
            status=OrderState.PLANNED.value if decision == "BUY" else OrderState.REJECTED.value,
        )
