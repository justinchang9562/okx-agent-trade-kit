from __future__ import annotations

import argparse
import copy
import json
from datetime import UTC, datetime

import yaml

from backtest.backtester import Backtester
from execution.mcp_backend import public_read_only_mcp_backend
from execution.read_only_backend import ReadOnlyBackend
from trading_agent.config import load_config


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Multi-symbol OOS research evaluation; never submits orders.")
    value.add_argument("--symbols", nargs="*")
    value.add_argument("--days", nargs="*", type=int, choices=(7, 30, 90))
    return value


def aggregate(results: list[dict]) -> dict:
    performances = [item["result"]["performance"] for item in results if "performance" in item["result"]]
    return {
        "runs": len(performances),
        "net_pnl": sum(float(item["net_pnl"]) for item in performances),
        "total_trades": sum(int(item["total_trades"]) for item in performances),
        "fees_paid": sum(float(item["fees_paid"]) for item in performances),
        "worst_maximum_drawdown": max((float(item["maximum_drawdown"]) for item in performances), default=0),
    }


def aggregate_groups(results: list[dict]) -> dict[str, dict]:
    keys = sorted({f"{item['strategy']}::{item['scenario']}" for item in results})
    return {
        key: aggregate([
            item for item in results
            if f"{item['strategy']}::{item['scenario']}" == key
        ])
        for key in keys
    }


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    config = load_config()
    experiments = yaml.safe_load((config.root / "config" / "experiments.yaml").read_text(encoding="utf-8"))
    symbols = tuple(item.upper() for item in (args.symbols or experiments["evaluation"]["symbols"]))
    days = tuple(args.days or experiments["evaluation"]["windows_days"])
    if not set(symbols).issubset(config.symbols):
        raise SystemExit("SYMBOL_NOT_CONFIGURED")
    backend = ReadOnlyBackend(public_read_only_mcp_backend())
    runs: list[dict] = []
    strategies = [experiments["production_strategy"], *experiments.get("research_strategies", [])]
    try:
        for strategy in strategies:
            for scenario, multiplier in experiments["evaluation"]["cost_multipliers"].items():
                rules = copy.deepcopy(config.rules)
                for field in ("fee_pct", "spread_pct", "slippage_pct"):
                    rules["backtest"][field] = float(rules["backtest"][field]) * float(multiplier)
                tester = Backtester(
                    backend,
                    rules,
                    config.root / "data_cache",
                    strategy_version=strategy,
                    allow_research_strategy=True,
                )
                for symbol in symbols:
                    for window in days:
                        runs.append({
                            "strategy": strategy,
                            "scenario": scenario,
                            "cost_multiplier": float(multiplier),
                            "symbol": symbol,
                            "days": window,
                            "result": tester.run(symbol, window),
                            "walk_forward": tester.walk_forward(symbol, window),
                        })
    finally:
        backend.close()
    output = {
        "generated_at": datetime.now(UTC).isoformat(),
        "production_strategy_frozen": experiments["production_strategy"],
        "research_only": True,
        "automatic_promotion": False,
        "runs": runs,
        "aggregate": aggregate(runs),
        "aggregate_by_strategy_scenario": aggregate_groups(runs),
        "promotion_gate": experiments["promotion_gate"],
    }
    destination = config.root / "output" / "research" / "multi_symbol_evaluation.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"output": str(destination), "aggregate": output["aggregate"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
