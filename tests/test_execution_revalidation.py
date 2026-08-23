from __future__ import annotations

from dataclasses import replace

from backtest.backtester import Backtester
from data.models import Candle
from tests.test_approval_revalidation import ReadOnlyBackend


def test_gap_open_is_revalidated_without_rerunning_strategy(market, long_signal) -> None:
    rules = {
        "environment": "demo",
        "market": {"stale_after_seconds": 120},
        "risk": {
            "risk_per_trade": .005, "max_position_pct": .1, "max_daily_loss_pct": .03,
            "max_consecutive_losses": 3, "max_open_positions": 2, "max_total_exposure_pct": .9,
        },
        "trade": {"minimum_risk_reward": 1.5},
        "scalping": {
            "max_spread_pct": .1, "minimum_signal_score": 7,
            "minimum_signal_strength": .7, "cooldown_seconds": 0,
        },
        "backtest": {
            "initial_equity": 10_000, "fee_pct": .001, "spread_pct": .0005,
            "slippage_pct": .0003,
        },
    }
    base = market.candles["1m"][0].timestamp_ms
    primary = tuple(
        Candle(base + i * 60_000, 105, 106, 99, 100, 100, confirmed=True)
        for i in range(70)
    )
    frames = {"1m": primary, "3m": primary, "5m": primary}
    backend = ReadOnlyBackend()
    tester = Backtester(backend, rules)
    calls = 0

    def frozen_signal(_market):
        nonlocal calls
        calls += 1
        return replace(
            long_signal,
            entry_price=100,
            suggested_stop=98,
            suggested_take_profit=103,
            risk_reward=1.5,
        )

    tester.strategy.analyze = frozen_signal
    trades, _initial, skipped = tester._simulate("BTC-USDT", frames, market.instrument)
    assert not trades
    assert calls > 0
    assert sum(skipped.values()) > 0
    assert "INVALID_QUANTIZED_PROTECTION_PRICES" in skipped
