from __future__ import annotations

import argparse
import json

from trading_agent.config import load_config
from trading_agent.demo_lifecycle import REAL_DEMO_CONFIRMATION, DemoLifecycleVerifier
from trading_agent.orchestrator import TradingOrchestrator


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Fail-closed OKX Demo lifecycle verification. Default mode never submits an order."
    )
    value.add_argument("--symbol", default="BTC-USDT")
    value.add_argument("--resume-plan")
    value.add_argument("--submit-real-demo", action="store_true")
    value.add_argument("--confirm", default="")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    config = load_config()
    blocked = False
    with TradingOrchestrator(config) as orchestrator:
        verifier = DemoLifecycleVerifier(orchestrator, config.root / "audit_exports" / "demo_lifecycle")
        if args.resume_plan:
            result = verifier.resume(args.resume_plan)
        else:
            precheck = verifier.precheck(args.symbol)
            result = precheck
            if args.submit_real_demo:
                try:
                    result = verifier.submit(
                        precheck,
                        submit_flag=True,
                        confirmation=args.confirm,
                    )
                except PermissionError as exc:
                    blocked = True
                    result = precheck | {"submission": "BLOCKED", "reason": str(exc)}
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        if args.submit_real_demo and (args.confirm != REAL_DEMO_CONFIRMATION or blocked):
            return 2
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
