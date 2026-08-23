# Local Web Dashboard API v1

Status: **FROZEN for W7**

Base URL: `http://127.0.0.1:8000/api/v1`

OpenAPI: `GET /api/v1/openapi.json` and `/api/v1/docs`

This API is a local control plane over one authoritative Python
`TradingOrchestrator`. It is not an exchange proxy. No route accepts OKX
credentials, arbitrary order payloads, strategy results, risk overrides or Live
execution instructions.

## Transport and security contract

1. The supported production server binds only to `127.0.0.1`, with one process
   and one worker.
2. Trusted hosts are `127.0.0.1`, `localhost` and the test host. A local-client
   middleware rejects non-loopback clients.
3. No CORS middleware is installed. Browsers use same-origin requests.
4. `GET /session` creates an in-memory, eight-hour session. Its cookie is
   `HttpOnly`, `SameSite=Strict`; the response body contains the CSRF token.
5. All read endpoints except API documentation require the session cookie.
   Every action endpoint additionally requires `X-CSRF-Token`.
6. ARM, Agent start, AUTO enable, approval preview and approval submission also
   require a currently authenticated WebSocket client acknowledgement. A stale or disconnected
   stream fails `WEBSOCKET_NOT_FRESH` before the action handler runs.
7. WebSocket is read-only except for strict
   `{ "type": "heartbeat.ack", "sequence": <last_seen> }` acknowledgements. Any
   other client message is rejected and can never trigger a control action. The
   handshake checks both the session cookie and a localhost Origin.
8. Browser projections recursively redact sensitive key names and authorization
   values. Backend/adapter/executor objects are never serialized.
9. Service errors use `{ "error": { "code": "...", "message": "..." } }`.
   Unknown upstream error text is not exposed.

## State model

The following domains are orthogonal and are returned in `status.control`:

| Domain | Values | Safety rule |
|---|---|---|
| `environment` | `DEMO`, `LIVE` | W0-W7 accepts only `DEMO`; Live returns `LIVE_NOT_CONFIGURED`. |
| `live_setup_state` | `NOT_CONFIGURED`, `READ_ONLY_READY`, `TRADE_PERMISSION_READY` | W0-W7 reports `NOT_CONFIGURED`. |
| `execution_state` | `DISARMED`, `ARMED` | Restart always writes `DISARMED`. |
| `agent_runtime_state` | `STOPPED`, `RUNNING`, `DEGRADED`, `STALE` | Runtime failure disarms and degrades fail-closed. |
| `trading_mode` | `STOPPED`, `DRY_RUN`, `MANUAL_APPROVAL`, `AUTO` | `AUTO` maps to Core `AUTO_DEMO`; it is default-off. |
| `connection_state` | `CONNECTED`, `STALE`, `DISCONNECTED` | Derived from backend health; not execution authority. |
| `kill_switch_active` | boolean | `true` blocks new entries/AUTO, DISARMS and stops runtime. |

Every transition is persisted to `control_audit_log`. Startup forces
`DEMO / DISARMED / STOPPED / AUTO disabled` before recovery, preserves only a
valid scan interval, and preserves an ACTIVE Kill Switch until explicit reset.

## Read endpoints

| Method and path | Purpose |
|---|---|
| `GET /session` | Create/replace the local browser session and CSRF token. |
| `GET /status` | Service controls, compatible Core status and permanent Live lock. |
| `GET /health` | Fresh Core capability and trading-eligibility evaluation. |
| `GET /account` | Demo account, balances and separated exposure projection. |
| `GET /scanner` | Last scanner result without causing a new scan. |
| `GET /signals?limit=` | Persisted normalized rule-score signals. |
| `GET /plans?status=&limit=` | TradePlan history with additive UI lifecycle. |
| `GET /plans/pending` | Non-expired server-owned pending plan IDs. |
| `GET /orders` | OKX open orders and persisted Agent lifecycle. |
| `GET /positions` | Wallet and Agent-managed positions as separate data. |
| `GET /trades` | Fills/fees/slippage/PnL; unknown values remain null. |
| `GET /logs?limit=` | Tail of the recursively redacted structured log. |
| `GET /audit-log?limit=` | Persisted service control transition audit. |
| `GET /settings` | Sanitized rules and runtime view. |

## Action endpoints

| Method and path | Body | Contract |
|---|---|---|
| `POST /scanner/run` | none | Executes the Core scan; never submits. |
| `POST /analyze/{symbol}` | none | Creates a Core TradePlan; never submits. |
| `POST /environment` | `{environment}` | `DEMO` only; `LIVE` is locked. |
| `POST /mode` | `{mode}` | Select mode; STOPPED also stops/disarms. |
| `POST /agent/start` | none | Starts scan/reconciliation after a mode is selected. |
| `POST /agent/stop` | none | Stops and disarms; does not cancel protection. |
| `POST /execution/arm` | none | Requires fresh health and no risk blockers. |
| `POST /execution/disarm` | none | Immediately prevents new entries. |
| `POST /auto-demo/enable` | `{confirmation}` | Exact `ENABLE AUTO DEMO`; process/session only. |
| `POST /auto-demo/disable` | none | Disables AUTO and disarms if AUTO was selected. |
| `POST /kill-switch` | none | Stops/disarms/disables AUTO; preserves TP/SL. |
| `POST /kill-switch/reset` | `{confirmation}` | Exact `RESET KILL SWITCH`; safe STOPPED/DISARMED. |
| `POST /plans/{plan_id}/preview` | none | Fresh TTL/data/spread/risk/exposure/size/deviation preview plus one-time challenge. |
| `POST /plans/{plan_id}/approve` | `{approval_challenge}` | One-time, session/plan/preview-bound Demo approval; no order fields. |
| `POST /plans/{plan_id}/reject` | none | Rejects a still-pending plan. |
| `POST /backtest` | `{symbol,days,walk_forward}` | 7/30/90-day backtest or walk-forward. |
| `PUT /settings/runtime` | `{scan_interval_seconds}` | Allowlisted 5–3600 seconds only. |

Approval does not accept `symbol`, `side`, `amount`, entry, TP, SL or order type.
The 15-second challenge is consumed once and is not an order-parameter token.
Core reloads the persisted plan and performs fresh revalidation; material price,
size or risk drift requires a new preview. Duplicate plan approval remains
blocked by challenge consumption, plan-state CAS and client-order-ID idempotency.

Backtests use a separate one-thread service and public read-only MCP process.
They return `BACKTEST_BLOCKED_WHILE_EXECUTION_ARMED` while ARMED and fail closed
when an active runtime cannot safely share upstream read capacity.

## WebSocket v1

Endpoint: `ws://127.0.0.1:8000/api/v1/ws`

```json
{
  "schema_version": "1.0",
  "sequence": 42,
  "timestamp_ms": 1787414400000,
  "type": "control.state",
  "reason": "PASS",
  "data": {}
}
```

`sequence` is process-local and monotonic. Reconnect begins with a full
`snapshot`; clients replace their projection and then apply newer events.
Heartbeats are sent each second. The browser marks the stream stale after seven
seconds and acknowledges each received event with its last sequence. The server
independently requires a client ACK not older than eight seconds; successful
server sends do not refresh eligibility. Any fresh authenticated tab is enough,
while all stale tabs fail closed.
The same server-side freshness gate is used by AUTO DEMO and the DemoExecutor's
last-moment entry guard, so closing every authenticated control stream stops all
new automatic submissions without touching existing protection.

Stable event types:

- `snapshot`, `snapshot.error`, `heartbeat`
- `control.state`, `health.updated`, `account.updated`, `scanner.updated`
- `plan.created`, `plan.revalidated`, `plan.approval_result`, `plan.rejected`
- `orders.reconciled`, `orders.recovery_failed`
- `auto_demo.execution_result`, `risk.kill_switch`
- `backtest.completed`, `runtime.error`

Unknown future event types must be ignored. Additive fields may be added in v1;
removing or renaming existing fields requires a new API version.

## macOS App reuse boundary

A future macOS client should use the same HTTP actions and WebSocket envelope.
It must not link to MCP, read `.env`, construct exchange orders, or access SQLite
directly. Native UI may add local session bootstrap, confirmations and reconnect
logic, but Python remains the sole Strategy/Risk/Sizing/Decision/Execution owner.
