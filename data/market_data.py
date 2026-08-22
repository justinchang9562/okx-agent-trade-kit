from __future__ import annotations

from datetime import datetime, timezone

from data.models import MarketSnapshot
from data.normalizer import normalize_candles, normalize_market
from execution.base_backend import BaseBackend


class MarketDataError(RuntimeError):
    """Raised when market data is unavailable, incomplete, stale, or invalid."""


class MarketDataService:
    def __init__(self, backend: BaseBackend, rules: dict) -> None:
        self.backend = backend
        self.rules = rules

    def get_snapshot(self, symbol: str) -> MarketSnapshot:
        timeframes = [self.rules["timeframes"]["primary"], *self.rules["timeframes"]["confirmation"]]
        candles = {name: normalize_candles(self.backend.get_candles(symbol, name, 100)) for name in timeframes}
        market = normalize_market(
            symbol, self.backend.get_ticker(symbol), self.backend.get_orderbook(symbol, 5),
            self.backend.get_instrument(symbol), candles,
        )
        self.validate(market)
        return market

    def validate(self, market: MarketSnapshot) -> None:
        if market.price <= 0 or market.bid <= 0 or market.ask <= 0 or market.ask < market.bid:
            raise MarketDataError("DATA_INVALID:invalid price or book")
        minimum = int(self.rules["market"]["minimum_candles"])
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        if now_ms - market.timestamp_ms > int(self.rules["market"]["stale_after_seconds"]) * 1000:
            raise MarketDataError("DATA_INVALID:stale ticker")
        for timeframe, candles in market.candles.items():
            completed = [candle for candle in candles if candle.confirmed]
            if len(completed) < minimum:
                raise MarketDataError(f"DATA_INVALID:{timeframe}:minimum candles")
            unit = timeframe[-1]
            amount = int(timeframe[:-1])
            interval_ms = amount * ({"m": 60_000, "H": 3_600_000, "D": 86_400_000}.get(unit, 0))
            if interval_ms <= 0:
                raise MarketDataError(f"DATA_INVALID:{timeframe}:unsupported timeframe")
            last_completed_close = completed[-1].timestamp_ms + interval_ms
            # A completed N-minute candle can legitimately be almost N minutes old.
            max_candle_age = interval_ms + int(self.rules["market"]["stale_after_seconds"]) * 1000
            if now_ms - last_completed_close > max_candle_age:
                raise MarketDataError(f"DATA_INVALID:{timeframe}:stale candles")
            timestamps = [candle.timestamp_ms for candle in candles]
            if timestamps != sorted(timestamps) or len(set(timestamps)) != len(timestamps):
                raise MarketDataError(f"DATA_INVALID:{timeframe}:timestamps")
            for candle in candles:
                if min(candle.open, candle.high, candle.low, candle.close) <= 0:
                    raise MarketDataError(f"DATA_INVALID:{timeframe}:price")
                if candle.high < max(candle.open, candle.close, candle.low) or candle.low > min(candle.open, candle.close, candle.high):
                    raise MarketDataError(f"DATA_INVALID:{timeframe}:ohlc")
                if candle.volume < 0:
                    raise MarketDataError(f"DATA_INVALID:{timeframe}:volume")
