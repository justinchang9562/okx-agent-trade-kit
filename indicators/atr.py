from __future__ import annotations

from collections.abc import Sequence

from data.models import Candle


def atr(candles: Sequence[Candle], period: int = 14) -> float:
    if len(candles) <= period:
        raise ValueError(f"ATR requires at least {period + 1} candles")
    ranges: list[float] = []
    for index in range(1, len(candles)):
        candle = candles[index]
        previous_close = candles[index - 1].close
        ranges.append(max(
            candle.high - candle.low,
            abs(candle.high - previous_close),
            abs(candle.low - previous_close),
        ))
    value = sum(ranges[:period]) / period
    for true_range in ranges[period:]:
        value = ((value * (period - 1)) + true_range) / period
    return value
