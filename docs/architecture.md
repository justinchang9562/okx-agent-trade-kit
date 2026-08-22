# Architecture

```text
                         Codex
                           |
                    Control Layer
                           |
                    Trading Agent
                           |
                     Quant Engine
              +------------+------------+
              |            |            |
          Indicators    Strategy       Risk (veto)
                                        |
                                 Position Sizing
                                        |
                                Decision / Plan
                                        |
                                     Approval
                                        |
                                  Order Manager
                                        |
                                    OKX Adapter
                         +--------------+--------------+
                         |              |              |
                    Demo MCP           CLI        Native API
                         |              |              |
                                      OKX
```

`TradingOrchestrator` loads all three configuration files, retrieves normalized
market/account snapshots, validates them, invokes the production strategy, asks
the risk manager, sizes an approved signal, creates an immutable trade plan, and
persists observations. It never silently submits.

The quant engine sees `MarketSnapshot`, `AccountSnapshot`, and `Instrument`, not
MCP/CLI response shapes. Backend changes are limited to `execution/` and data
normalization. Codex must not bypass this local pipeline.

Strategy produces a `Signal` only. It imports no execution code. Order Manager
contains no indicators. DemoExecutor rechecks the environment and backend Demo
capability at the final boundary. Plan IDs are deterministic per symbol, side,
strategy, and minute, providing retry deduplication.

Core v0.2 persists `TradePlan -> OrderLifecycle -> ManagedPosition -> Trade` in
SQLite. Approval is only by `plan_id`; fresh data and risk are re-evaluated at
approval. The lifecycle is committed before submission, and an uncertain
transport result becomes `SUBMISSION_UNKNOWN` until client-order-ID
reconciliation. Wallet assets remain account exposure and never become managed
positions merely because their balances are non-zero.

`LocalControlAPI` is the future UI boundary. It exposes status, health, scan,
analyze, pending-plan approval/rejection, positions, orders, trades, and stop.
It does not expose MCP objects, order-manager internals, or credentials.
