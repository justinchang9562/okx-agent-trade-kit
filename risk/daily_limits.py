from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DailyRiskState:
    realized_pnl: float = 0.0
    consecutive_losses: int = 0
    last_trade_timestamp_ms: int | None = None
    open_position_count: int = 0


def daily_loss_reached(equity: float, state: DailyRiskState, max_loss_pct: float) -> bool:
    return equity > 0 and state.realized_pnl <= -(equity * max_loss_pct)
