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

Core v0.3.0 persists `TradePlan -> OrderLifecycle -> ManagedPosition -> Trade` in
SQLite. Approval is only by `plan_id`; fresh data and risk are re-evaluated at
approval. The lifecycle is committed before submission, and an uncertain
transport result becomes `SUBMISSION_UNKNOWN` until client-order-ID
reconciliation. Wallet assets remain account exposure and never become managed
positions merely because their balances are non-zero. Submitted and otherwise
reserved entry orders occupy position slots and projected notional before fill.
Entry and protective-order identifiers link fill reconciliation to the eventual
atomic `ManagedPosition`/trade/order `CLOSED` transition.

Order transitions use optimistic compare-and-swap updates, preventing a stale
worker from overwriting a newer state. Repository connections use WAL, bounded
busy waits and per-repository reentrant locks. The shared MCP stdio client admits
one request at a time with a timeout, so concurrent future control requests
cannot cross-match JSON-RPC responses. Its stderr is drained continuously and
discarded without logging content.

`LocalControlAPI` is the future UI boundary. It exposes status, health, scan,
analyze, pending-plan approval/rejection, positions, orders, trades, and stop.
It does not expose MCP objects, order-manager internals, or credentials.
Web handlers call this facade through `TradingService` and do not access the MCP
process or SQLite connections directly. Health distinguishes durable system capability
from current trading eligibility such as STOPPED, exposure limits, reserved
slots, kill switches, unknown submissions and unprotected positions.

The W0-W7 local Web control plane is an additional client boundary, not a new
trading engine:

```text
React / TypeScript / Vite (localhost browser)
        | same-origin session + CSRF + authenticated WebSocket ACK
FastAPI /api/v1 (127.0.0.1, one process / one worker)
        |
TradingService (compatibility state, scheduler, audit)
        +-> one-thread authoritative Core -> LocalControlAPI -> TradingOrchestrator
        +-> one-thread BacktestService -> independent read-only MCP backend
```

Scheduler reconciliation and AUTO approval use the serialized Core worker.
Backtests cannot occupy it, cannot reuse the credential-bearing execution object,
and are blocked while execution is ARMED. Browser state is a projection; Core
TradePlan/order/managed-position rows remain authoritative. The WebSocket accepts
only strict heartbeat acknowledgements and never accepts control actions. See
[API/WebSocket v1](web-dashboard-api-v1.md).
