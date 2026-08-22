from __future__ import annotations

from datetime import datetime, timezone

import pytest

from data.models import AccountSnapshot, Balance, Candle, Instrument, MarketSnapshot
from strategies.signal import Signal


@pytest.fixture
def instrument() -> Instrument:
    return Instrument("BTC-USDT", "BTC", "USDT", 0.00005, 0.00000001, 0.1)


def candles(direction: float = 1.0, count: int = 100) -> tuple[Candle, ...]:
    output = []
    for index in range(count):
        close = 100.0 + (direction * index * 0.1)
        volume = 100.0 if index < count - 1 else 300.0
        output.append(Candle(1_700_000_000_000 + index * 60_000, close - direction * 0.05,
                             close + 0.2, close - 0.2, close, volume, confirmed=True))
    return tuple(output)


@pytest.fixture
def market(instrument: Instrument) -> MarketSnapshot:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    primary = candles()
    return MarketSnapshot(
        "BTC-USDT", now_ms, primary[-1].close, primary[-1].close - 0.01, primary[-1].close + 0.01,
        10000, {"1m": primary, "3m": primary, "5m": primary}, instrument,
    )


@pytest.fixture
def account() -> AccountSnapshot:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return AccountSnapshot(now_ms, 10_000, 5_000, (Balance("USDT", 5_000, 5_000),))


@pytest.fixture
def long_signal(market: MarketSnapshot) -> Signal:
    return Signal(
        "BTC-USDT", market.timestamp_ms, "LONG", 8, 0.8, market.price,
        market.price - 2, market.price + 3, 1.5, ("test",), {"1m": "BULLISH"},
    )
