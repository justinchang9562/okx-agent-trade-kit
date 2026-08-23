# Changelog

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
