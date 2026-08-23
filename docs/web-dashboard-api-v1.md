# Local Dashboard API v1

Base URL: http://127.0.0.1:8000/api/v1

This is a local control plane over one authoritative Python trading agent. It is not an exchange
proxy and accepts no credentials, arbitrary order payload, strategy result, risk override or Live
instruction.

## Main session API

| Method | Path | Meaning |
|---|---|---|
| GET | /session/status | User-level session, latest preflight, market and account freshness |
| POST | /session/start | Atomic Preflight then START or Resume |
| POST | /session/pause | Block entries, cancel Agent entry orders, preserve positions and SL/TP |
| POST | /session/stop | End AUTO session, cancel Agent entry orders, preserve positions and SL/TP |
| POST | /session/flatten | Idempotently close Agent-managed positions only, then STOP |

START and FLATTEN require a fresh authenticated Dashboard WebSocket acknowledgement at the control
boundary. Once START succeeds, a temporary browser disconnect does not revoke session-level
authorization; automatic execution continues until PAUSE, STOP, FLATTEN, Kill Switch or a fail-closed
runtime condition. The final executor independently requires RUNNING, ARMED, Demo and fresh realtime
market state.

No session endpoint accepts symbol, side, quantity, entry, stop, take profit or order type. Those
remain server-owned outputs of Strategy, RiskManager, Position Sizing and Trade Decision.

## State model

status.control includes:

| Domain | Values |
|---|---|
| session_state | STOPPED, RUNNING, PAUSED, FLATTENING, DEGRADED |
| environment | DEMO; LIVE requests fail LIVE_NOT_CONFIGURED |
| execution_state | DISARMED, ARMED; internal safety state |
| order lifecycle | includes nonterminal `CANCEL_REQUESTED` and `SUBMISSION_UNKNOWN` |
| flatten lifecycle | may expose `FLATTEN_INCOMPLETE` or `PROTECTION_CLEANUP_INCOMPLETE` |
| agent_runtime_state | STOPPED, RUNNING, DEGRADED, STALE; internal |
| trading_mode | compatibility/internal; main UI never selects it |
| connection_state | CONNECTED, STALE, DISCONNECTED |
| kill_switch_active | persistent emergency entry block |

Startup always writes STOPPED / DISARMED / AUTO off before reconciliation. Kill Switch persists.

## Read API

| Path | Projection |
|---|---|
| GET /status | Session, Core, market, observability and permanent Live lock |
| GET /health | Capability and current risk/trading blockers |
| GET /account | Fresh Demo account and exposure |
| GET /orders | Open orders tagged AGENT or EXTERNAL plus Agent lifecycle |
| GET /positions | Agent-managed positions and separated external wallet inventory |
| GET /fills | Recent fills tagged AGENT or EXTERNAL |
| GET /trades | Persisted completed/active trade evidence and PnL |
| GET /scanner | Latest confirmed-candle evaluation |
| GET /signals | Persisted signals |
| GET /plans | TradePlan history |
| GET /logs | Redacted logs |
| GET /audit-log | Persisted control transitions |
| GET /settings | Sanitized configuration |

GET /session remains the local browser session/CSRF bootstrap endpoint.

## Advanced compatibility API

The legacy environment, mode, agent start/stop, arm/disarm, AUTO enable/disable and per-plan
preview/approval routes remain available for CLI diagnostics and compatibility. OpenAPI marks the
main legacy control routes deprecated. The React primary flow does not call them.

Backtest, Walk Forward, scanner, analyze and manual approval remain Advanced/Research/Developer
features and never enter the automatic session hot path.

## Transport security

- Production binds only 127.0.0.1 with one process and one worker.
- Trusted Host and loopback middleware reject non-local access; CORS is not enabled.
- The session cookie is HttpOnly and SameSite=Strict; writes require CSRF.
- WebSocket handshakes require the local Origin and session cookie.
- WebSocket accepts only heartbeat acknowledgements and cannot issue control actions.
- Browser projections recursively redact credential-like keys and bearer values.
- Errors use stable error codes and do not expose upstream secret-bearing text.

## WebSocket events

Clients receive an initial snapshot, then monotonic process-local events:

- control.state, session.started, session.paused, session.stopped, session.flatten
- health.updated, account.updated, orders.updated, positions.updated, fills.updated
- scanner.updated, auto_session.execution_result
- orders.reconciled, risk.position_unprotected, market.stale, runtime.error
- heartbeat

Unknown additive event types must be ignored. External mobile/manual account changes appear through
the three-second Account Synchronizer; external objects are visible but never automatically
controlled.

## Live

Live Trading is LOCKED / NOT IMPLEMENTED. There is no Live toggle or Live executor in this API.
