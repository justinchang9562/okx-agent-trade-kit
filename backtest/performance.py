from __future__ import annotations

import math
from statistics import mean, pstdev
from typing import Any

from backtest.simulator import SimulatedTrade


def _max_streak(values: list[bool]) -> int:
    best = current = 0
    for value in values:
        current = current + 1 if value else 0
        best = max(best, current)
    return best


def calculate_performance(trades: list[SimulatedTrade], initial_equity: float) -> dict[str, Any]:
    pnls = [trade.pnl for trade in trades]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    equity = initial_equity
    peak = initial_equity
    max_drawdown = 0.0
    equity_curve = [{"timestamp_ms": 0, "value": initial_equity}]
    drawdown_curve = [{"timestamp_ms": 0, "value": 0.0}]
    for trade, value in zip(trades, pnls, strict=True):
        equity += value
        peak = max(peak, equity)
        drawdown = (peak - equity) / peak if peak else 0.0
        max_drawdown = max(max_drawdown, drawdown)
        equity_curve.append({"timestamp_ms": trade.exit_timestamp_ms, "value": equity})
        drawdown_curve.append({"timestamp_ms": trade.exit_timestamp_ms, "value": drawdown})
    returns = [value / initial_equity for value in pnls]
    deviation = pstdev(returns) if len(returns) > 1 else 0.0
    downside = [min(value, 0.0) for value in returns]
    downside_deviation = math.sqrt(sum(value * value for value in downside) / len(downside)) if downside else 0.0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "total_trades": len(trades), "winning_trades": len(wins), "losing_trades": len(losses),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "net_pnl": sum(pnls), "gross_profit": gross_profit, "gross_loss": gross_loss,
        "average_win": mean(wins) if wins else 0.0, "average_loss": mean(losses) if losses else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss else (float("inf") if gross_profit else 0.0),
        "expectancy": mean(pnls) if pnls else 0.0,
        "sharpe_ratio": (mean(returns) / deviation * math.sqrt(len(returns))) if deviation else 0.0,
        "sortino_ratio": (mean(returns) / downside_deviation * math.sqrt(len(returns))) if downside_deviation else 0.0,
        "maximum_drawdown": max_drawdown,
        "average_holding_time_seconds": mean([trade.holding_time_seconds for trade in trades]) if trades else 0.0,
        "largest_win": max(wins) if wins else 0.0, "largest_loss": min(losses) if losses else 0.0,
        "consecutive_wins": _max_streak([value > 0 for value in pnls]),
        "consecutive_losses": _max_streak([value < 0 for value in pnls]),
        "fees_paid": sum(trade.fees for trade in trades),
        "slippage_cost": sum(trade.slippage_cost for trade in trades),
        "ending_equity": initial_equity + sum(pnls),
        "equity_curve": equity_curve,
        "drawdown_curve": drawdown_curve,
    }
