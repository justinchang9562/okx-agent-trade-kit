from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from data.models import Candle


@dataclass(frozen=True)
class MarketStructure:
    swing_high: float
    swing_low: float
    support: float
    resistance: float
    trend: str


def analyze_structure(candles: Sequence[Candle], lookback: int = 20) -> MarketStructure:
    if len(candles) < lookback:
        raise ValueError(f"market structure requires at least {lookback} candles")
    selected = candles[-lookback:]
    half = max(2, lookback // 2)
    earlier = selected[:half]
    recent = selected[half:]
    earlier_mid = sum(c.close for c in earlier) / len(earlier)
    recent_mid = sum(c.close for c in recent) / len(recent)
    tolerance = earlier_mid * 0.0005
    trend = "BULLISH" if recent_mid > earlier_mid + tolerance else "BEARISH" if recent_mid < earlier_mid - tolerance else "SIDEWAYS"
    swing_high = max(c.high for c in selected)
    swing_low = min(c.low for c in selected)
    return MarketStructure(
        swing_high=swing_high, swing_low=swing_low,
        support=max(c.low for c in selected[-5:]) if trend == "BULLISH" else min(c.low for c in selected[-5:]),
        resistance=min(c.high for c in selected[-5:]) if trend == "BEARISH" else max(c.high for c in selected[-5:]),
        trend=trend,
    )
