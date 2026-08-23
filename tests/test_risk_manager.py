from dataclasses import replace
from datetime import datetime, timezone

import pytest

from risk.daily_limits import DailyRiskState
from risk.exposure import ExposureSnapshot
from risk.risk_manager import RiskManager


def rules() -> dict:
    return {
        "environment": "demo", "market": {"stale_after_seconds": 120},
        "trade": {"minimum_risk_reward": 1.5},
        "scalping": {"max_spread_pct": 0.10, "minimum_signal_score": 7,
                     "minimum_confidence": 0.7, "cooldown_seconds": 60},
        "risk": {"max_daily_loss_pct": 0.03, "max_consecutive_losses": 3,
                 "max_open_positions": 2, "max_total_exposure_pct": .9},
    }


def test_risk_pass(long_signal, market, account) -> None:
    assert RiskManager(rules()).evaluate(long_signal, market, account, DailyRiskState()).approved


@pytest.mark.parametrize(("state", "reason"), [
    (DailyRiskState(realized_pnl=-301), "DAILY_KILL_SWITCH_ACTIVE"),
    (DailyRiskState(consecutive_losses=3), "MAX_CONSECUTIVE_LOSSES_REACHED"),
    (DailyRiskState(open_position_count=2), "MAX_OPEN_POSITIONS_REACHED"),
])
def test_risk_limits(long_signal, market, account, state, reason) -> None:
    assert RiskManager(rules()).evaluate(long_signal, market, account, state).reason == reason


def test_spread_missing_stop_rr_cooldown_and_stale(long_signal, market, account) -> None:
    manager = RiskManager(rules())
    assert manager.evaluate(long_signal, replace(market, bid=99, ask=101), account, DailyRiskState()).reason == "SPREAD_TOO_WIDE"
    assert manager.evaluate(replace(long_signal, suggested_stop=None), market, account, DailyRiskState()).reason == "MISSING_STOP_LOSS"
    assert manager.evaluate(replace(long_signal, suggested_take_profit=long_signal.entry_price + 1), market, account, DailyRiskState()).reason == "RISK_REWARD_BELOW_MINIMUM"
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    assert manager.evaluate(long_signal, market, account, DailyRiskState(last_trade_timestamp_ms=now)).reason == "COOLDOWN_ACTIVE"
    assert manager.evaluate(long_signal, replace(market, timestamp_ms=1), account, DailyRiskState()).reason == "STALE_DATA"


def test_exact_minimum_rr_tolerates_float_roundoff(long_signal, market, account) -> None:
    signal = replace(
        long_signal, entry_price=2421.8, suggested_stop=2406.9338146606674,
        suggested_take_profit=2444.099278008999,
    )
    assert RiskManager(rules()).evaluate(signal, market, account, DailyRiskState()).reason == "PASS"


def test_wallet_exposure_is_separate_from_managed_position_count(long_signal, market, account) -> None:
    exposure = ExposureSnapshot(9_100, 0, 9_100, .91, ("BTC",), ())
    decision = RiskManager(rules()).evaluate(
        long_signal, market, account, DailyRiskState(open_position_count=0), exposure=exposure
    )
    assert decision.reason == "MAX_TOTAL_EXPOSURE_REACHED"
