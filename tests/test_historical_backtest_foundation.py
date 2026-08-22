from dataclasses import replace

import pytest

from backtest.cost_models import FixedFeeModel, FixedSlippageModel, FixedSpreadModel
from backtest.walk_forward import evaluate, windows
from data.historical import validate_history
from tests.conftest import candles


def test_historical_integrity_detects_gap_duplicate_and_unconfirmed() -> None:
    valid = candles(count=20)
    assert validate_history("BTC-USDT", "1m", valid).valid
    gap = valid[:5] + valid[6:]
    report = validate_history("BTC-USDT", "1m", gap)
    assert not report.valid and report.status == "DATA_GAP" and report.missing_bars == 1
    duplicate = valid + (valid[-1],)
    assert validate_history("BTC-USDT", "1m", duplicate).duplicates == 1
    unconfirmed = valid[:-1] + (replace(valid[-1], confirmed=False),)
    assert validate_history("BTC-USDT", "1m", unconfirmed).unconfirmed_bars == 1


def test_cost_models_are_explicit_and_injectable() -> None:
    assert FixedFeeModel(.001).cost(100, 110, 2) == pytest.approx(.42)
    assert FixedSpreadModel(.002).half_spread_pct(0) == pytest.approx(.001)
    assert FixedSlippageModel(.0003).pct(0, "buy") == pytest.approx(.0003)


def test_walk_forward_windows_are_out_of_sample() -> None:
    result = windows(100, 40, 20, 20)
    assert len(result) == 3
    assert all(item.train_end == item.test_start for item in result)
    evaluated = evaluate(tuple(range(100)), lambda train, test: {
        "train_last": train[-1], "test_first": test[0]
    }, 40, 20, 20)
    assert evaluated[0]["out_of_sample"] == {"train_last": 39, "test_first": 40}
