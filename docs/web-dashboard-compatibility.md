# Local Web Dashboard v2.1 — Core Compatibility Review

Status: W0 frozen baseline

Source: `OKX_Local_Web_Dashboard_Plan_v2.1.pdf` and Core Hardening v0.2.1

Baseline tests: 61 passed, 0 failed

## Non-negotiable boundaries

- `TradingOrchestrator` remains the only trading core owner.
- FastAPI and React do not implement indicators, strategy, risk, sizing, plan
  construction, order construction, execution or reconciliation.
- Browser writes are action requests against server-owned identifiers. Approval
  accepts only an existing `plan_id` and invokes the existing fresh revalidation
  path.
- W0-W7 supports Demo execution only. Live is represented as
  `LIVE_NOT_CONFIGURED` and `LOCKED`; there is no Live execution branch.
- The production server binds to `127.0.0.1`, uses one process/worker, has no
  permissive CORS, and protects HTTP writes and the WebSocket handshake with a
  same-origin session plus CSRF token.
- The browser never receives OKX credentials or backend objects.

## Orthogonal service states

The service facade adds the PDF state domains without rewriting Core enums:

| Service domain | v1 values | Core compatibility |
|---|---|---|
| Environment | `DEMO`, `LIVE` | Core v0.2.1 remains Demo; `LIVE` requests fail `LIVE_NOT_CONFIGURED`. |
| Live setup | `NOT_CONFIGURED`, `READ_ONLY_READY`, `TRADE_PERMISSION_READY` | W0-W7 always reports `NOT_CONFIGURED`. |
| Execution | `DISARMED`, `ARMED` | Additive hard gate composed with `AgentState.require_new_entry_allowed`. |
| Agent runtime | `STOPPED`, `RUNNING`, `DEGRADED`, `STALE` | Service scheduler state; Core is set to `STOPPED` whenever new entries must stop. |
| Trading mode | `STOPPED`, `DRY_RUN`, `MANUAL_APPROVAL`, `AUTO` | Maps to existing `RuntimeMode`; PDF `AUTO` maps to Core `AUTO_DEMO`. |
| Connection | `CONNECTED`, `STALE`, `DISCONNECTED` | Derived from backend health and WebSocket heartbeat; it is never a write permission. |
| Kill switch | `OFF`, `ON` | `ON` blocks new entries, stops scanning and disarms, while preserving protective orders. |

The persisted service control record is audit/history input, not authority to
restore risk. Every backend start forcibly writes `DEMO + DISARMED + STOPPED +
AUTO disabled`. In particular, `ARMED` is never restored.

## TradePlan compatibility view

Core persistence remains authoritative:

| Core value/evidence | API/UI lifecycle |
|---|---|
| Plan row `PLANNED`, before expiry | `PENDING_APPROVAL` |
| In-flight call to existing `approve_plan` | transient `REVALIDATING` event |
| Plan rejected with `PLAN_EXPIRED` | `EXPIRED` |
| Plan/order `APPROVED`, `SUBMITTED`, `OPEN`, `PARTIALLY_FILLED`, `FILLED`, `REJECTED`, `CANCELLED`, `CLOSED` | same stable value |
| `SUBMISSION_UNKNOWN` while reconciliation is requested | stored `SUBMISSION_UNKNOWN` plus transient `RECONCILING` event |
| Managed protection state `PROTECTED` | `PROTECTED` annotation |
| `POSITION_UNPROTECTED` or non-protected active managed position | `UNPROTECTED` critical annotation |

No existing order transition is renamed or bypassed. Transient UI phases are
events/projections, not new persisted order states.

## Single-owner call model

FastAPI creates exactly one `TradingService` in application lifespan. That
service owns one `TradingOrchestrator` and a one-thread executor. HTTP requests,
the scheduler, reconciliation, backtests and WebSocket snapshot refreshes submit
Core work through that executor. Direct route access to MCP, SQLite, OrderManager
or credentials is prohibited.

## W1-W7 acceptance mapping

- W1: typed `/api/v1` read APIs, protected action API, OpenAPI and single-owner
  service foundation.
- W2: responsive Vite dashboard, typed client, reconnecting WebSocket, stale write
  lockout and read-only panels.
- W3: action-only environment/mode/execution/agent transitions; Live stays locked.
- W4: server-plan-only preview/confirm/reject approval center.
- W5: session-only, explicit, default-off AUTO DEMO; scheduler and kill switch.
- W6: backtest, redacted logs and safe runtime settings.
- W7: freeze schemas/events, integration/security tests and macOS reuse document.
