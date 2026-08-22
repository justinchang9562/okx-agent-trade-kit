import pytest

from risk.position_sizing import calculate_position_size


def test_risk_sizing_and_position_cap(account, instrument) -> None:
    result = calculate_position_size(account, instrument, 100, 98, 0.005, 0.10)
    assert result.approved
    assert result.quantity == pytest.approx(10.0)  # 10% equity cap = 1000 USDT
    assert result.notional_usdt == pytest.approx(1000)
    assert result.capped


def test_minimum_and_precision(account, instrument) -> None:
    tiny = calculate_position_size(account, instrument, 100_000_000, 99_999_999, 0.000001, 0.000001)
    assert not tiny.approved
    assert tiny.reason == "BELOW_MINIMUM_ORDER_SIZE"
