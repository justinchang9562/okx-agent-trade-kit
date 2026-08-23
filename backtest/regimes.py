from __future__ import annotations

from typing import Any

from backtest.performance import calculate_performance
from backtest.simulator import SimulatedTrade
from data.models import Candle


def regime_performance(
    trades: list[SimulatedTrade], candles: tuple[Candle, ...], initial_equity: float,
) -> dict[str, Any]:
    """Objective volatility buckets using realized candle-range tertiles."""
    realized = sorted((candle.high - candle.low) / candle.close for candle in candles if candle.close > 0)
    if not realized:
        return {"method": "realized_range_quantile", "status": "DATA_UNAVAILABLE", "buckets": {}}
    lower = realized[int((len(realized) - 1) / 3)]
    upper = realized[int((len(realized) - 1) * 2 / 3)]
    by_timestamp = {
        candle.timestamp_ms: (candle.high - candle.low) / candle.close
        for candle in candles if candle.close > 0
    }
    buckets: dict[str, list[SimulatedTrade]] = {"range": [], "trend": [], "high_volatility": []}
    for trade in trades:
        value = by_timestamp.get(trade.entry_timestamp_ms)
        if value is None:
            continue
        bucket = "range" if value <= lower else "high_volatility" if value > upper else "trend"
        buckets[bucket].append(trade)
    return {
        "method": "realized_range_quantile",
        "thresholds": {"lower": lower, "upper": upper},
        "buckets": {
            name: calculate_performance(values, initial_equity)
            for name, values in buckets.items()
        },
    }
