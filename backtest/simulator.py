from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SimulatedTrade:
    entry_timestamp_ms: int
    exit_timestamp_ms: int
    entry: float
    exit: float
    size: float
    pnl: float
    fees: float
    slippage_cost: float
    outcome: str

    @property
    def holding_time_seconds(self) -> float:
        return (self.exit_timestamp_ms - self.entry_timestamp_ms) / 1000
