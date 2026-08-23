from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from data.models import MarketSnapshot
from indicators.atr import atr
from indicators.volume import volume_ratio
from strategies.base_strategy import BaseStrategy
from strategies.scalping_strategy import ScalpingStrategy
from strategies.signal import Signal

PRODUCTION_STRATEGY_VERSION = "scalping_v1_baseline"
RESEARCH_QUALITY_VERSION = "scalping_v1_quality_research"


def research_quality_score(signal: Signal, market: MarketSnapshot, rules: dict) -> float:
    """Independent experimental quality score; never used by production entry gates."""
    primary = tuple(candle for candle in market.candles["1m"] if candle.confirmed)
    bullish = sum(value == "BULLISH" for value in signal.timeframes.values()) / max(1, len(signal.timeframes))
    ratio = volume_ratio([candle.volume for candle in primary], 20)
    volume_quality = min(1.0, ratio / max(2.0, float(rules["scalping"]["minimum_volume_ratio"])))
    atr_pct = atr(primary, 14) / market.price * 100 if market.price > 0 else 0.0
    volatility_quality = max(0.0, 1.0 - abs(atr_pct - 0.3) / 0.6)
    spread_limit = float(rules["scalping"]["max_spread_pct"])
    spread_quality = max(0.0, 1.0 - market.spread_pct / spread_limit) if spread_limit > 0 else 0.0
    structure_quality = 1.0 if signal.timeframes.get("1m") == "BULLISH" else 0.5
    return round(
        0.25 * bullish
        + 0.20 * volume_quality
        + 0.20 * volatility_quality
        + 0.20 * spread_quality
        + 0.15 * structure_quality,
        6,
    )


class ResearchQualityScalpingStrategy(BaseStrategy):
    def __init__(self, rules: dict) -> None:
        self.rules = rules
        self._baseline = ScalpingStrategy(rules)

    def analyze(self, market: MarketSnapshot) -> Signal:
        baseline = self._baseline.analyze(market)
        return replace(
            baseline,
            strategy=RESEARCH_QUALITY_VERSION,
            research_quality_score=research_quality_score(baseline, market, self.rules),
        )


STRATEGY_REGISTRY: dict[str, Callable[[dict], BaseStrategy]] = {
    PRODUCTION_STRATEGY_VERSION: ScalpingStrategy,
    RESEARCH_QUALITY_VERSION: ResearchQualityScalpingStrategy,
}


def build_strategy(version: str, rules: dict, *, production: bool = False) -> BaseStrategy:
    if production and version != PRODUCTION_STRATEGY_VERSION:
        raise ValueError("RESEARCH_STRATEGY_NOT_PROMOTED")
    try:
        factory = STRATEGY_REGISTRY[version]
    except KeyError as exc:
        raise ValueError(f"UNKNOWN_STRATEGY_VERSION:{version}") from exc
    return factory(rules)
