from __future__ import annotations

from collections.abc import Sequence


def volume_ma(volumes: Sequence[float], period: int = 20) -> float:
    if len(volumes) < period:
        raise ValueError(f"volume MA requires at least {period} values")
    return sum(float(value) for value in volumes[-period:]) / period


def volume_ratio(volumes: Sequence[float], period: int = 20) -> float:
    if len(volumes) <= period:
        raise ValueError(f"volume ratio requires at least {period + 1} values")
    baseline = volume_ma(volumes[:-1], period)
    return float(volumes[-1]) / baseline if baseline > 0 else 0.0
