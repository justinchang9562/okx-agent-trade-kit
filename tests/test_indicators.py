import pytest

from data.models import Candle
from indicators.atr import atr
from indicators.ema import ema
from indicators.macd import macd
from indicators.rsi import rsi
from indicators.volume import volume_ratio
from indicators.vwap import vwap


def test_ema_constant_series() -> None:
    assert ema([5.0] * 30, 9) == pytest.approx(5.0)


def test_rsi_known_monotonic_boundaries() -> None:
    assert rsi(list(range(20)), 14) == 100.0
    assert rsi(list(range(20, 0, -1)), 14) == 0.0


def test_macd_is_bullish_for_accelerating_series() -> None:
    result = macd([float(index * index) for index in range(50)])
    assert result.value > result.signal
    assert result.histogram > 0


def test_atr_vwap_and_volume() -> None:
    candles = tuple(Candle(i, 10, 12, 9, 11, 2) for i in range(30))
    assert atr(candles) == pytest.approx(3.0)
    assert vwap(candles) == pytest.approx((12 + 9 + 11) / 3)
    assert volume_ratio([2.0] * 20 + [4.0]) == pytest.approx(2.0)
