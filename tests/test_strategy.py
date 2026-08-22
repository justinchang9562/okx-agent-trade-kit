from dataclasses import replace

from tests.conftest import candles
from strategies.scalping_strategy import ScalpingStrategy


def rules() -> dict:
    return {
        "timeframes": {"primary": "1m", "confirmation": ["3m", "5m"]},
        "scalping": {"minimum_volume_ratio": 1.2, "minimum_signal_score": 7},
        "trade": {"minimum_risk_reward": 1.5},
    }


def test_strategy_long(market) -> None:
    result = ScalpingStrategy(rules()).analyze(market)
    assert result.side == "LONG"
    assert result.score >= 7
    assert result.suggested_stop < result.entry_price < result.suggested_take_profit


def test_strategy_bearish(market) -> None:
    down = candles(-1)
    bearish = replace(market, price=down[-1].close, bid=down[-1].close - .01, ask=down[-1].close + .01,
                      candles={"1m": down, "3m": down, "5m": down})
    result = ScalpingStrategy(rules()).analyze(bearish)
    assert result.side == "BEARISH"
    assert result.suggested_stop is None


def test_strategy_hold(market) -> None:
    flat = tuple(replace(item, open=100, high=100.1, low=99.9, close=100, volume=100) for item in candles())
    neutral = replace(market, price=100, bid=99.99, ask=100.01,
                      candles={"1m": flat, "3m": flat, "5m": flat})
    assert ScalpingStrategy(rules()).analyze(neutral).side == "HOLD"
