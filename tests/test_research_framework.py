from __future__ import annotations

import pytest
import yaml

from backtest.regimes import regime_performance
from backtest.simulator import SimulatedTrade
from data.models import Candle
from scripts.run_research_evaluation import aggregate, aggregate_groups
from strategies.registry import (
    PRODUCTION_STRATEGY_VERSION,
    RESEARCH_QUALITY_VERSION,
    build_strategy,
)
from trading_agent.config import load_config


def test_production_registry_rejects_unpromoted_research_strategy() -> None:
    rules = load_config().rules
    assert build_strategy(PRODUCTION_STRATEGY_VERSION, rules, production=True)
    with pytest.raises(ValueError, match="RESEARCH_STRATEGY_NOT_PROMOTED"):
        build_strategy(RESEARCH_QUALITY_VERSION, rules, production=True)


def test_research_quality_is_independent_metadata_not_a_production_gate(market) -> None:
    rules = load_config().rules
    baseline = build_strategy(PRODUCTION_STRATEGY_VERSION, rules, production=True).analyze(market)
    research = build_strategy(RESEARCH_QUALITY_VERSION, rules).analyze(market)

    assert baseline.strategy == PRODUCTION_STRATEGY_VERSION
    assert baseline.research_quality_score is None
    assert research.strategy == RESEARCH_QUALITY_VERSION
    assert 0 <= float(research.research_quality_score) <= 1
    assert research.side == baseline.side
    assert research.score == baseline.score
    assert research.signal_strength == baseline.signal_strength


def test_regime_buckets_are_objective_realized_range_quantiles() -> None:
    candles = tuple(
        Candle(index * 60_000, 100, 100 + width, 100 - width, 100, 1)
        for index, width in enumerate((0.1, 0.5, 1.0))
    )
    trades = [
        SimulatedTrade(candle.timestamp_ms, candle.timestamp_ms + 60_000, 100, 101, 1, 1, 0, 0, "TP")
        for candle in candles
    ]

    result = regime_performance(trades, candles, 10_000)

    assert result["method"] == "realized_range_quantile"
    assert {
        name: bucket["total_trades"]
        for name, bucket in result["buckets"].items()
    } == {"range": 1, "trend": 1, "high_volatility": 1}


def test_experiment_matrix_and_aggregate_preserve_per_run_evidence() -> None:
    experiments = yaml.safe_load((load_config().root / "config" / "experiments.yaml").read_text())
    assert experiments["evaluation"]["symbols"] == ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
    assert experiments["evaluation"]["windows_days"] == [7, 30, 90]
    assert tuple(experiments["evaluation"]["cost_multipliers"].values()) == (1.0, 1.5, 2.0)
    assert experiments["promotion_gate"]["automatic_promotion"] is False

    performance = {
        "net_pnl": 2.0,
        "total_trades": 3,
        "fees_paid": 0.5,
        "maximum_drawdown": 0.1,
    }
    result = aggregate([
        {"result": {"performance": performance}},
        {"result": {"performance": {
            "net_pnl": 3.0,
            "total_trades": 1,
            "fees_paid": 0.25,
            "maximum_drawdown": 0.2,
        }}},
    ])
    assert result == {
        "runs": 2,
        "net_pnl": 5.0,
        "total_trades": 4,
        "fees_paid": 0.75,
        "worst_maximum_drawdown": 0.2,
    }
    grouped = aggregate_groups([
        {"strategy": "baseline", "scenario": "base", "result": {"performance": performance}},
        {"strategy": "research", "scenario": "base", "result": {"performance": performance}},
    ])
    assert set(grouped) == {"baseline::base", "research::base"}
    assert all(value["runs"] == 1 for value in grouped.values())
