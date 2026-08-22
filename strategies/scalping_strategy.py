from __future__ import annotations

from data.models import Candle, MarketSnapshot
from indicators.atr import atr
from indicators.ema import ema
from indicators.macd import macd
from indicators.market_structure import analyze_structure
from indicators.rsi import rsi
from indicators.volume import volume_ratio
from indicators.vwap import vwap
from strategies.base_strategy import BaseStrategy
from strategies.signal import Signal


class ScalpingStrategy(BaseStrategy):
    def __init__(self, rules: dict) -> None:
        self.rules = rules

    @staticmethod
    def _completed(candles: tuple[Candle, ...]) -> tuple[Candle, ...]:
        return tuple(candle for candle in candles if candle.confirmed)

    @staticmethod
    def _timeframe_bias(candles: tuple[Candle, ...]) -> str:
        closes = [candle.close for candle in candles]
        if len(closes) < 21:
            return "UNKNOWN"
        fast, slow = ema(closes, 9), ema(closes, 21)
        return "BULLISH" if fast > slow else "BEARISH" if fast < slow else "NEUTRAL"

    def analyze(self, market: MarketSnapshot) -> Signal:
        primary_name = str(self.rules["timeframes"]["primary"])
        primary = self._completed(market.candles[primary_name])
        closes = [candle.close for candle in primary]
        volumes = [candle.volume for candle in primary]
        ema9, ema21, ema50 = ema(closes, 9), ema(closes, 21), ema(closes, 50)
        current_rsi = rsi(closes, 14)
        current_macd = macd(closes)
        current_vwap = vwap(primary, min(50, len(primary)))
        current_atr = atr(primary, 14)
        current_volume_ratio = volume_ratio(volumes, 20)
        structure = analyze_structure(primary)
        biases = {name: self._timeframe_bias(self._completed(market.candles[name])) for name in ("3m", "5m")}

        score = 0
        reasons: list[str] = []
        if ema9 > ema21 and ema21 >= ema50:
            score += 2
            reasons.append("EMA9 > EMA21 >= EMA50")
        elif ema9 > ema21:
            score += 1
            reasons.append("EMA9 > EMA21")
        if market.price > current_vwap:
            score += 2
            reasons.append("price above VWAP")
        if 45 <= current_rsi <= 70:
            score += 1
            reasons.append(f"RSI reasonable ({current_rsi:.1f})")
        if current_macd.histogram > 0 and current_macd.value > current_macd.signal:
            score += 1
            reasons.append("MACD bullish")
        minimum_volume = float(self.rules["scalping"]["minimum_volume_ratio"])
        if current_volume_ratio >= minimum_volume:
            score += 2
            reasons.append(f"volume ratio {current_volume_ratio:.2f}")
        bullish_confirmations = sum(bias != "BEARISH" for bias in biases.values())
        score += bullish_confirmations
        if bullish_confirmations:
            reasons.append(f"higher timeframes non-bearish {bullish_confirmations}/2")

        minimum_score = int(self.rules["scalping"]["minimum_signal_score"])
        severely_bearish = ema9 < ema21 and market.price < current_vwap and current_macd.histogram < 0
        side = "LONG" if score >= minimum_score and not severely_bearish else "BEARISH" if severely_bearish else "HOLD"
        stop = None
        take_profit = None
        rr = None
        if side == "LONG":
            atr_stop = market.price - (1.5 * current_atr)
            structure_stop = structure.swing_low - (0.1 * current_atr)
            stop = max(0.0, min(atr_stop, structure_stop))
            risk_distance = market.price - stop
            rr = float(self.rules["trade"]["minimum_risk_reward"])
            take_profit = market.price + (risk_distance * rr)
        signal_strength = min(1.0, score / 10.0)
        return Signal(
            symbol=market.symbol, timestamp_ms=market.timestamp_ms, side=side, score=score,
            signal_strength=signal_strength, entry_price=market.price, suggested_stop=stop,
            suggested_take_profit=take_profit, risk_reward=rr, reasons=tuple(reasons),
            timeframes={"1m": structure.trend, **biases},
        )
