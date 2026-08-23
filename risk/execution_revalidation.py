from __future__ import annotations

from dataclasses import dataclass, replace

from data.models import AccountSnapshot, MarketSnapshot
from risk.daily_limits import DailyRiskState
from risk.exposure import ExposureSnapshot
from risk.position_sizing import SizingResult, calculate_position_size
from risk.price_quantization import quantize_long_execution_prices, risk_reward
from risk.risk_manager import RiskDecision, RiskManager
from strategies.signal import Signal


@dataclass(frozen=True)
class ExecutionRevalidation:
    approved: bool
    reason: str
    signal: Signal
    risk: RiskDecision
    sizing: SizingResult
    entry: float = 0.0
    stop: float = 0.0
    take_profit: float = 0.0
    risk_reward: float = 0.0


def build_executable_long_plan(
    signal: Signal,
    market: MarketSnapshot,
    account: AccountSnapshot,
    state: DailyRiskState,
    rules: dict,
    *,
    executable_entry: float | None = None,
    duplicate: bool = False,
    exposure: ExposureSnapshot | None = None,
    risk_manager: RiskManager | None = None,
    evaluation_timestamp_ms: int | None = None,
) -> ExecutionRevalidation:
    """Pure executable-price, RR, risk and sizing validation shared by runtime and backtests."""
    rejected_sizing = SizingResult(False, "SIGNAL_OR_RISK_REJECTED")
    if signal.side != "LONG":
        reason = f"SIGNAL_{signal.side}"
        return ExecutionRevalidation(False, reason, signal, RiskDecision(False, reason), rejected_sizing)
    if signal.suggested_stop is None or signal.suggested_take_profit is None:
        reason = "MISSING_STOP_LOSS" if signal.suggested_stop is None else "MISSING_TAKE_PROFIT"
        return ExecutionRevalidation(False, reason, signal, RiskDecision(False, reason), rejected_sizing)
    try:
        entry, stop, take_profit = quantize_long_execution_prices(
            executable_entry if executable_entry is not None else market.ask,
            signal.suggested_stop,
            signal.suggested_take_profit,
            market.instrument.tick_size,
        )
        final_rr = risk_reward(entry, stop, take_profit)
    except ValueError as exc:
        reason = str(exc)
        return ExecutionRevalidation(False, reason, signal, RiskDecision(False, reason), rejected_sizing)

    executable_signal = replace(
        signal,
        entry_price=entry,
        suggested_stop=stop,
        suggested_take_profit=take_profit,
        risk_reward=final_rr,
    )
    manager = risk_manager or RiskManager(rules)
    risk = manager.evaluate(
        executable_signal,
        market,
        account,
        state,
        duplicate=duplicate,
        exposure=exposure,
        evaluation_timestamp_ms=evaluation_timestamp_ms,
    )
    if not risk.approved:
        return ExecutionRevalidation(
            False, risk.reason, executable_signal, risk, SizingResult(False, risk.reason),
            entry, stop, take_profit, final_rr,
        )
    sizing = calculate_position_size(
        account,
        market.instrument,
        entry,
        stop,
        float(rules["risk"]["risk_per_trade"]),
        float(rules["risk"]["max_position_pct"]),
    )
    return ExecutionRevalidation(
        sizing.approved,
        sizing.reason,
        executable_signal,
        risk,
        sizing,
        entry,
        stop,
        take_profit,
        final_rr,
    )
