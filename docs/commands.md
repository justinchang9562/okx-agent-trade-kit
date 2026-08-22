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
Forward testing is intentionally disabled; a future scheduler must call this
same orchestrator and retain explicit automation policy and kill switches.
