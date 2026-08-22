# OKX Agent Trade Kit — Core v0.2

A deterministic, minute-level spot scalping system whose current operating path
uses the configured **OKX Demo Trade MCP**. It follows Path B: Codex is a control
and development surface; trading decisions are made by the local Python pipeline.

```text
User -> Codex -> Trading Agent -> Market Data -> Indicators -> Strategy
     -> Risk -> Position Sizing -> Decision -> Trade Plan -> Approval
     -> Order Manager -> OKX Adapter -> Demo MCP / CLI / Native API -> OKX
```

The system is rule-based and explainable. It does not claim profitability and is
not HFT. Strategy cannot place orders, risk can veto every trade, spot bearish
signals never become synthetic shorts, and live trading is locked.

## Current backend states

- MCP: primary backend; starts the existing `okx-mcp-demo-trade` stdio wrapper.
  Demo market, account, spot query, and guarded order methods are supported.
- CLI: installed public Demo market path can be detected; not selected for
  private execution and therefore reported `PARTIALLY_WORKING`.
- Native REST/WebSocket: extension boundary only, `NOT_CONFIGURED`.
- Live: `LOCKED`; no live executor implementation is enabled.

No credential is stored in this repository. The MCP wrapper continues to source
the separate Demo credentials from the existing local secure configuration.

## Install

Python 3.11+ is required. Using the already-installed `uv`:

```bash
uv sync --extra dev
```

Core configuration is in `config/trading_rules.yaml`, `config/symbols.yaml`, and
`config/environments.yaml`. Defaults are Demo, spot, 1m entry with 3m/5m
confirmation, explicit approval, 0.5% risk per trade, 10% maximum position
notional, a 3% daily kill switch, and three-loss suspension. These are
engineering defaults, not investment recommendations.

## Commands

```bash
uv run python -m trading_agent status
uv run python -m trading_agent health
uv run python -m trading_agent scan
uv run python -m trading_agent analyze BTC-USDT
uv run python -m trading_agent dry-run BTC-USDT
uv run python -m trading_agent positions
uv run python -m trading_agent orders
uv run python -m trading_agent pending
uv run python -m trading_agent approve PLAN_ID
uv run python -m trading_agent approve PLAN_ID --confirm "CONFIRM DEMO ORDER"
uv run python -m trading_agent reject PLAN_ID
uv run python -m trading_agent trades
uv run python -m trading_agent recover
uv run python -m trading_agent backtest BTC-USDT
uv run python -m trading_agent backtest BTC-USDT --days 30
uv run python -m trading_agent walk-forward BTC-USDT --days 7
uv run pytest
```

`dry-run` retrieves fresh Demo data and account state, calculates the complete
plan, and never calls order submission. `analyze` persists an executable plan
with a configurable TTL. The only execution entry is `approve PLAN_ID`: without
the exact confirmation it produces a fresh execution preview only. With exact
confirmation it re-fetches market/account state and repeats stale-data, spread,
exposure, daily-loss, consecutive-loss, duplicate, sizing, TTL, price-deviation
and pre-submit-slippage guards before `OrderManager` can submit.

The backtest uses paginated, integrity-checked real OKX historical OHLCV with an
ignored local cache, production indicators/strategy/risk/sizing, next-bar-open
execution, injectable fee/spread/slippage models, and conservative same-bar
stop/target ordering. Supported fetch horizons are 7, 30 and 90 days. The basic
walk-forward path reports rolling out-of-sample windows without parameter tuning.

## Data, monitoring and safety

SQLite `trading_agent.db` stores plans, persistent order transitions, managed
positions, actual/unknown fill fees, signals and trades. Wallet inventory is not
an Agent-managed position. `max_open_positions` counts only Agent-managed active
lifecycles; wallet exposure is separately limited by `max_total_exposure_pct`.
Structured events are written to `logs/trading_agent.log`; neither runtime file
is tracked.

The pipeline validates timestamps, staleness, missing data, prices, OHLC
integrity, candle count, spread, stop, target, risk/reward, position precision,
minimum size, account exposure, managed positions, daily loss, consecutive
losses, cooldown, duplicate plans, approval, Demo identity, and live gates.
Submission uncertainty is persisted and reconciled by client order ID instead
of being blindly retried. A filled entry without verified protection becomes
`POSITION_UNPROTECTED`. Failures produce HOLD/REJECT and do not submit an order.

See [architecture](docs/architecture.md), [strategy](docs/strategy.md),
[risk controls](docs/risk-management.md), [execution backends](docs/execution-backends.md),
[commands](docs/commands.md), and [Demo to live](docs/demo-to-live.md).
