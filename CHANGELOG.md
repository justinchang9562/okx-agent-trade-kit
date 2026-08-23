# Changelog

## 0.4.2 - 2026-08-23

- Consolidated Dashboard UX around session-level START, PAUSE, STOP, and FLATTEN controls.
- Added dedicated realtime market, signal history, approval, lifecycle, position, trade, and runtime views.
- Unified timestamp, price, quantity, USDT, percent, PnL, duration, optional-value, and identifier formatting.
- Added read-only managed-exposure, protection, display-dust, realtime runtime, and degraded-reason projections.
- Fixed macOS Python CA verification for OKX Demo WebSockets and normalized subscription request IDs to OKX's
  alphanumeric protocol requirement.
- Preserved the five top safety chips, Approval Center primary navigation, spacious visual layout, Demo-only MCP path, and frozen v0.4.1 trading core.

## 0.4.1 - 2026-08-23

- Made cancel acknowledgement nonterminal with persistent `CANCEL_REQUESTED` and bounded targeted
  reconciliation for cancel/fill races.
- Added a newly submitted order fast lane that immediately reconciles fill and strict protection
  state without weakening `SUBMISSION_UNKNOWN` or duplicate guards.
- Replaced the single flatten intent with ordered persistent attempts whose quantities derive only
  from deduplicated confirmed exit fills; protection cleanup is retried separately after closure.
- Hardened books5 sequence-reset recovery, interleaved WebSocket heartbeat handling, confirmed-candle
  queue backpressure, stale strategy-event rejection, and AccountSynchronizer shutdown behavior.

## 0.4.0 - 2026-08-23

Final product consolidation into an operator-controlled automatic Demo trading agent.

- Added one authoritative `AutoTradingSessionController` for START, PAUSE, STOP and idempotent
  FLATTEN ALL & STOP semantics.
- Moved per-trade human approval out of the main AUTO session while preserving final deterministic
  strategy, risk, sizing, freshness, slippage and OrderManager checks.
- Connected confirmed 1m strategy evaluation to the OKX official Public WebSocket local market state;
  3m/5m confirmation buffers and realtime execution revalidation remain mandatory.
- Added a 3-second single-flight account/order/fill synchronizer and explicit AGENT/EXTERNAL ownership.
- Strengthened protection verification to require active, correctly linked, tick-aware SL and TP with
  sufficient quantity.
- Simplified the Dashboard to session status plus START, PAUSE, STOP and FLATTEN ALL & STOP.

No actual Demo order was submitted during this implementation. Live remains locked and unimplemented.

## 0.3.0 - 2026-08-23

Hardened remediation release for the Local Web Dashboard v2.1 workstream.

- Added a default-read-only, explicitly gated real Demo lifecycle verification harness.
- Isolated backtests from the authoritative runtime worker and execution backend.
- Added capability-aware fill pagination, exact order lookup, persistent cursors, and fail-closed
  long-term reconciliation.
- Revalidated next-open backtest execution with the same production risk and sizing rules.
- Changed control freshness to authenticated browser ACKs and added single-use approval challenges.
- Added CI, repository safety checks, release/security documentation, and structured Dashboard views.
- Added multi-symbol, multi-window research evaluation scaffolding without promoting new parameters.

This release makes no profitability claim. Live order execution remains unavailable.
