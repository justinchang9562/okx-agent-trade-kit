from __future__ import annotations

import argparse
import json
from typing import Any

from backtest.backtester import Backtester
from trading_agent.config import load_config
from trading_agent.orchestrator import TradingOrchestrator


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, default=str, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m trading_agent", description="OKX Demo quantitative scalping agent")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "health", "scan", "positions", "orders", "pending", "trades", "recover", "calibration"):
        sub.add_parser(name)
    for name in ("analyze", "dry-run", "backtest", "walk-forward"):
        item = sub.add_parser(name)
        item.add_argument("symbol")
        if name in {"backtest", "walk-forward"}:
            item.add_argument("--days", type=int, choices=(7, 30, 90), default=7)
    approve = sub.add_parser("approve")
    approve.add_argument("plan_id")
    approve.add_argument("--confirm", default="")
    reject = sub.add_parser("reject")
    reject.add_argument("plan_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config()
    with TradingOrchestrator(config) as orchestrator:
        if args.command == "status":
            _print(orchestrator.get_status())
        elif args.command == "health":
            result = orchestrator.get_health()
            _print(result)
            return 0 if result["system"] in {"READY_FOR_DEMO_DRY_RUN", "READY_FOR_FIRST_CONTROLLED_DEMO_ORDER"} else 2
        elif args.command == "scan":
            _print(orchestrator.scan())
        elif args.command in {"analyze", "dry-run"}:
            plan = orchestrator.analyze(args.symbol, persist=args.command == "analyze")
            output = plan.as_dict()
            if args.command == "dry-run":
                output["order_submission"] = "DISABLED_DRY_RUN"
            elif plan.decision == "BUY" and plan.risk_approved:
                output["order_submission"] = "AWAITING_EXPLICIT_APPROVAL"
            else:
                output["order_submission"] = "NOT_EXECUTABLE"
            _print(output)
        elif args.command == "positions":
            _print(orchestrator.positions())
        elif args.command == "orders":
            _print(orchestrator.orders())
        elif args.command == "pending":
            _print(orchestrator.get_pending_plans())
        elif args.command == "trades":
            _print(orchestrator.get_trades())
        elif args.command == "calibration":
            _print(orchestrator.trade_store.signal_calibration())
        elif args.command == "approve":
            _print(orchestrator.approve_plan(args.plan_id, args.confirm))
        elif args.command == "reject":
            _print(orchestrator.reject_plan(args.plan_id))
        elif args.command == "recover":
            _print(orchestrator.recover())
        elif args.command == "backtest":
            _print(Backtester(orchestrator.adapter.backend, config.rules, config.root / "data_cache").run(
                args.symbol.upper(), days=args.days
            ))
        elif args.command == "walk-forward":
            _print(Backtester(orchestrator.adapter.backend, config.rules, config.root / "data_cache").walk_forward(
                args.symbol.upper(), days=args.days
            ))
    return 0
