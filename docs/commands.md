# Commands

- `status`: configuration and truthful backend/live state.
- `health`: config, database, backend, fresh market, account, risk and guards.
- `scan`: analyze every configured symbol; per-symbol failures return
  `DATA_UNAVAILABLE` and `REJECT`.
- `analyze SYMBOL`: full pipeline and persisted plan; no order submission.
- `dry-run SYMBOL`: same plan without persistence or submission.
- `positions`: wallet balances and separately identified Agent-managed positions.
- `orders`: remote open orders plus local persistent lifecycle.
- `pending`: persisted, still-pending trade plans.
- `approve PLAN_ID`: fresh preview only; `--confirm "CONFIRM DEMO ORDER"` is the
  exact approval boundary after all guards are rerun.
- `reject PLAN_ID`: reject an existing pending plan.
- `recover`: reconcile local active/unknown orders with OKX by client order ID.
- `trades` / `calibration`: actual-or-unknown trade fields and score buckets.
- `backtest SYMBOL --days {7,30,90}`: paginated historical OHLCV and cost models.
- `walk-forward SYMBOL --days {7,30,90}`: rolling out-of-sample evaluation.

Use `python -m trading_agent ...` or `uv run python -m trading_agent ...`.

The local Web Dashboard and API use:

- `uv run python -m trading_agent.web_server`: build the frontend if needed,
  then serve Dashboard and API on `http://127.0.0.1:8000` with one authoritative
  Core worker.
- `uv run python -m trading_agent.web_server --rebuild-frontend`: force a fresh
  production frontend build before starting.

AUTO DEMO is implemented through the Web runtime but is disabled on every
startup and requires its exact enable phrase, an authenticated fresh WebSocket,
Agent RUNNING and execution ARMED. It does not weaken the normal TradePlan,
revalidation, Risk Manager, idempotency or DemoExecutor path. Live is locked.

The controlled Demo lifecycle verifier is read-only by default:

```bash
uv run python scripts/verify_demo_lifecycle.py --symbol BTC-USDT
uv run python scripts/verify_demo_lifecycle.py --resume-plan PLAN_ID
```

The first command performs health, Demo identity, capability, market, account,
risk, sizing and preview checks and writes a redacted audit export. It never calls
`place_order`. Real Demo submission requires both `--submit-real-demo` and the
exact confirmation phrase documented by `--help`; it is intentionally excluded
from CI and was not executed as part of v0.3.0 remediation.

The research-only evaluation matrix is:

```bash
uv run python scripts/run_research_evaluation.py
```

It compares the frozen baseline and registered research candidates using an
independent public read-only backend for BTC/ETH/SOL across 7/30/90 days, cost
stress scenarios and rolling walk-forward windows. It never promotes a strategy
or submits an order.
