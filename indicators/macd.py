from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from indicators.ema import ema_series


@dataclass(frozen=True)
class MACD:
    value: float
    signal: float
    histogram: float


def macd(values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> MACD:
    if len(values) < slow + signal:
        raise ValueError(f"MACD requires at least {slow + signal} values")
    fast_line = ema_series(values, fast)
    slow_line = ema_series(values, slow)
    line = [fast_line[i] - slow_line[i] for i in range(len(values))]
    signal_line = ema_series(line, signal)
    return MACD(value=line[-1], signal=signal_line[-1], histogram=line[-1] - signal_line[-1])
