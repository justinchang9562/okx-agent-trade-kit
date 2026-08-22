import sys

from trading_agent.cli import main

raise SystemExit(main(["backtest", sys.argv[1] if len(sys.argv) > 1 else "BTC-USDT"]))
