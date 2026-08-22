from __future__ import annotations

from collections.abc import Sequence

from data.models import Candle


def vwap(candles: Sequence[Candle], period: int | None = None) -> float:
    selected = candles[-period:] if period else candles
    if not selected:
        raise ValueError("VWAP requires candles")
    volume = sum(candle.volume for candle in selected)
    if volume <= 0:
        raise ValueError("VWAP requires positive volume")
    return sum(((c.high + c.low + c.close) / 3.0) * c.volume for c in selected) / volume
