from __future__ import annotations

from collections.abc import Sequence


def ema_series(values: Sequence[float], period: int) -> list[float]:
    if period <= 0 or not values:
        raise ValueError("period must be positive and values non-empty")
    multiplier = 2.0 / (period + 1)
    output = [float(values[0])]
    for value in values[1:]:
        output.append((float(value) - output[-1]) * multiplier + output[-1])
    return output


def ema(values: Sequence[float], period: int) -> float:
    if len(values) < period:
        raise ValueError(f"EMA requires at least {period} values")
    return ema_series(values, period)[-1]
