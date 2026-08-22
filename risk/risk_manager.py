from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from data.models import AccountSnapshot, MarketSnapshot
from risk.daily_limits import DailyRiskState, daily_loss_reached
from risk.exposure import ExposureSnapshot
from strategies.signal import Signal


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str


class RiskManager:
    def __init__(self, rules: dict) -> None:
        self.rules = rules

    def evaluate(
        self,
        signal: Signal,
        market: MarketSnapshot,
        account: AccountSnapshot,
        state: DailyRiskState,
        duplicate: bool = False,
        exposure: ExposureSnapshot | None = None,
        evaluation_timestamp_ms: int | None = None,
    ) -> RiskDecision:
        if str(self.rules["environment"]).lower() not in {"demo", "live"}:
            return RiskDecision(False, "ENVIRONMENT_INVALID")
        if signal.side != "LONG":
            return RiskDecision(False, f"SIGNAL_{signal.side}")
        now_ms = evaluation_timestamp_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
        stale_after = int(self.rules["market"]["stale_after_seconds"]) * 1000
        if market.timestamp_ms <= 0 or now_ms - market.timestamp_ms > stale_after:
            return RiskDecision(False, "STALE_DATA")
        if account.equity_usdt <= 0 or account.available_usdt <= 0:
            return RiskDecision(False, "ACCOUNT_DATA_UNAVAILABLE")
        if duplicate:
            return RiskDecision(False, "DUPLICATE_ORDER")
        if signal.suggested_stop is None:
            return RiskDecision(False, "MISSING_STOP_LOSS")
        if signal.suggested_take_profit is None:
            return RiskDecision(False, "MISSING_TAKE_PROFIT")
        if signal.entry_price <= signal.suggested_stop:
            return RiskDecision(False, "INVALID_STOP_LOSS")
        actual_rr = (signal.suggested_take_profit - signal.entry_price) / (signal.entry_price - signal.suggested_stop)
        if actual_rr + 1e-9 < float(self.rules["trade"]["minimum_risk_reward"]):
            return RiskDecision(False, "INSUFFICIENT_RISK_REWARD")
        if market.spread_pct > float(self.rules["scalping"]["max_spread_pct"]):
            return RiskDecision(False, "SPREAD_TOO_WIDE")
        if signal.score < int(self.rules["scalping"]["minimum_signal_score"]):
            return RiskDecision(False, "SIGNAL_SCORE_TOO_LOW")
        minimum_strength = float(self.rules["scalping"].get(
            "minimum_signal_strength", self.rules["scalping"].get("minimum_confidence", 0.0)
        ))
        if signal.signal_strength < minimum_strength:
            return RiskDecision(False, "SIGNAL_STRENGTH_TOO_LOW")
        risk_rules = self.rules["risk"]
        if daily_loss_reached(account.equity_usdt, state, float(risk_rules["max_daily_loss_pct"])):
            return RiskDecision(False, "DAILY_KILL_SWITCH_ACTIVE")
        if state.consecutive_losses >= int(risk_rules["max_consecutive_losses"]):
            return RiskDecision(False, "MAX_CONSECUTIVE_LOSSES_REACHED")
        if state.open_position_count >= int(risk_rules["max_open_positions"]):
            return RiskDecision(False, "MAX_OPEN_POSITIONS_REACHED")
        if exposure is not None and exposure.total_exposure_pct > float(risk_rules.get("max_total_exposure_pct", 1.0)):
            return RiskDecision(False, "MAX_TOTAL_EXPOSURE_REACHED")
        cooldown_ms = int(self.rules["scalping"]["cooldown_seconds"]) * 1000
        if state.last_trade_timestamp_ms and now_ms - state.last_trade_timestamp_ms < cooldown_ms:
            return RiskDecision(False, "COOLDOWN_ACTIVE")
        return RiskDecision(True, "PASS")
