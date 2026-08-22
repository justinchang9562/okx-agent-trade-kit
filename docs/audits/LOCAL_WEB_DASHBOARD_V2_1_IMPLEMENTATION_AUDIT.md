# OKX Local Web Dashboard v2.1 — W0–W7 Implementation Audit

Generated: 2026-08-23 00:30 Asia/Taipei

Core baseline: v0.2.1 (`10e533b`)

Implementation branch: `feature/local-web-dashboard-v2.1`
Scope: W0–W7 only; W8 Live is excluded and locked

## 1. Executive summary

The local Web Dashboard plan W0–W7 is implemented as a compatibility and
control layer over the existing hardened Core. React and FastAPI do not contain
Strategy, Risk, Sizing, Decision or Execution logic. One `TradingService` owns
one `TradingOrchestrator`; every Core operation runs through one worker thread.

The supported startup is:

```bash
uv run python -m trading_agent.web_server
```

It serves the production Vite build and `/api/v1` on `127.0.0.1:8000`. If the
ignored frontend build is absent, the command installs/builds it from the locked
npm manifest. Live remains `LIVE_NOT_CONFIGURED / LOCKED`.

Final status:

| Workstream | Status | Evidence |
|---|---|---|
| W0 compatibility review | WORKING | Frozen mapping in `web-dashboard-compatibility.md`; Core enums retained. |
| W1 Service/FastAPI/OpenAPI | WORKING | Single-owner facade, local-only API v1, tests and OpenAPI route. |
| W2 React/Vite/WebSocket | WORKING | Production build, authenticated real WebSocket, desktop/mobile render. |
| W3 controls | WORKING | Environment/mode/runtime/execution transitions and persisted audit. |
| W4 manual approval | WORKING | Existing plan ID only, fresh Core preview and exact confirmation. |
| W5 AUTO DEMO/Kill Switch | WORKING | Deterministic mocked lifecycle; default-off and control-stream fail-closed. |
| W6 Backtest/Logs/Settings | WORKING | UI/API plus real 7-day paginated backtest and redaction tests. |
| W7 v1 freeze/security | WORKING | API/WS contract, security review and integration evidence below. |
| W8 Live | LOCKED / NOT IMPLEMENTED | No verified Live backend and no Live action branch. |

No Demo or Live order was submitted during this implementation or verification.

## 2. Architecture delivered

```text
Browser (React + TypeScript + Vite)
    | same-origin session + CSRF
    | authenticated read-only WebSocket
FastAPI /api/v1 on 127.0.0.1
    |
TradingService
    |-- orthogonal control-state compatibility projection
    |-- single-thread Core executor
    |-- runtime scheduler / AUTO policy / kill switch
    |-- redacted browser projection / event journal
    |
LocalControlAPI
    |
one TradingOrchestrator
    |
existing Market -> Indicators -> Strategy -> Risk -> Sizing -> Decision
    -> TradePlan -> OrderManager -> DemoExecutor -> OKXAdapter -> Demo MCP
```

FastAPI routes never import the exchange backend, OrderManager, risk modules,
strategies or database repositories. SQLite and MCP remain behind the service
and Core boundary.

## 3. W0 compatibility and state semantics

No Core order state was renamed. The UI maps `PLANNED` to
`PENDING_APPROVAL`, annotates expiry, and represents revalidation/reconciliation
as transient events. Persistent order lifecycle remains:

`PLANNED -> APPROVED -> SUBMITTED -> OPEN/PARTIALLY_FILLED/FILLED -> CLOSED`

with existing `SUBMISSION_UNKNOWN`, `POSITION_UNPROTECTED`, `REJECTED`, and
`CANCELLED` branches.

The service adds orthogonal Environment, Live setup, Execution, Agent runtime,
Trading mode and Connection projections. `AUTO` maps to Core `AUTO_DEMO`.
Startup overwrites persisted controls with `DEMO / DISARMED / STOPPED / AUTO
disabled`; an old `ARMED` record is never restored.

## 4. Service ownership and concurrency

- One FastAPI lifespan creates one `TradingService`.
- The service creates one `TradingOrchestrator` and a
  `ThreadPoolExecutor(max_workers=1)`.
- HTTP, scheduler, recovery, backtest and WebSocket snapshot database reads are
  serialized through that worker.
- Control actions have a separate reentrant action lock, preventing ARM/Kill or
  mode transitions from interleaving into an invalid state.
- Service control state and its audit record are committed in one SQLite
  transaction.
- A Core synchronization error invokes a compensating safe reset.
- Kill Switch state is persisted before waiting for Core, and DemoExecutor's
  final guard reads that state immediately before submission.

## 5. API and security boundary

- Listen address is hard-coded to `127.0.0.1`; worker count is one.
- Trusted Host and loopback-client middleware reject DNS rebinding/non-local
  access.
- No CORS middleware is installed.
- Browser sessions are random, in-memory, bounded and expire after eight hours.
- Cookie is `HttpOnly`, `SameSite=Strict`; writes require a matching CSRF header.
- WebSocket checks the session plus localhost Origin and is read-only.
- ARM, Agent start, AUTO enable, preview and approval require a server-observed
  fresh WebSocket.
- TradingService and DemoExecutor independently reject new entries when every
  authenticated control stream is disconnected/stale.
- CSP, frame denial, MIME sniff protection, referrer, opener and permissions
  headers are set on responses.
- Recursive browser sanitization removes credential-named keys, bearer values
  and authorization content. Log-tail output uses the same sanitizer.
- Settings expose an allowlist; only scan interval 5–3600 seconds is mutable.
- No endpoint accepts credentials or a generic exchange order body.

## 6. Manual approval semantics

The browser sends only a URL `plan_id` and, at the final step, the exact phrase
`CONFIRM DEMO ORDER`. It cannot supply symbol, side, amount, entry, stop, target
or order type. The first click calls the existing Core approval method without
confirmation to obtain a fresh preview. The final click calls it again, so delay
inside the modal cannot reuse stale market/risk state.

Core performs plan load/status/TTL, fresh market/account, stale/spread guards,
fresh signal, tick-safe executable prices, risk/reward, daily/consecutive-loss,
managed slots, wallet/reserved exposure, size, duplicate, price deviation and
pre-submit slippage checks before the existing atomic idempotency boundary.

## 7. AUTO DEMO and Kill Switch

AUTO is usable but intentionally default-off and process-session-only:

1. Exact `ENABLE AUTO DEMO` is required.
2. AUTO mode must be selected.
3. Agent must be RUNNING.
4. Demo execution must be ARMED after fresh eligibility.
5. Backend and authenticated control stream must stay fresh.
6. Scheduler creates Core plans and approves only their server-owned plan IDs
   through the same fresh Core approval path.

Closing every authenticated WebSocket makes AUTO incapable of submission at the
service and final executor gates. Kill Switch disables AUTO, stops runtime and
DISARMS. It does not cancel or modify persisted protective TP/SL identifiers.
Reset requires `RESET KILL SWITCH` and returns to STOPPED/DISARMED.

## 8. Dashboard functions

The responsive single-page UI contains:

- persistent safety bar for Demo/Mode/Execution/WebSocket;
- Overview health, account equity/availability, managed slots, pending plans and
  exposure;
- Scanner plus persisted Signals;
- TradePlan Approval Center with TTL/status and two-step modal;
- persistent order lifecycle;
- wallet and managed-position separation;
- trades/fills/fees/PnL projection;
- paginated Backtest/Walk-Forward form and result;
- redacted runtime log and control transition audit;
- safe settings, runtime controls, AUTO phrase and Kill Switch.

At desktop and 390px mobile widths the production output was rendered with
headless Chrome. A real WebSocket upgrade was accepted after the missing runtime
WebSocket dependency was discovered and added.

## 9. Real Demo read-only evidence

Observed through the running local `/api/v1` service on 2026-08-23. Operations
were limited to market/account/order/history reads.

| Evidence | Result |
|---|---|
| Root production page | HTTP 200, `text/html` |
| Service environment | DEMO |
| Startup execution | DISARMED |
| Live projection | LOCKED |
| System capability | READY |
| Real WebSocket | Upgrade accepted; UI `CONNECTED` |
| Demo equity | 95,771.32 USDT at observation time |
| Available USDT | 5,000 |
| Wallet | BTC 1, ETH 1, OKB 100, USDT 5,000 |
| Agent-managed positions | 0 |
| Position slots in use | 0 / 2 |
| OKX open orders | 0 |
| Agent order lifecycle rows | 0 |
| Live switch request | HTTP 423 `LIVE_NOT_CONFIGURED` |
| ARM without WebSocket | HTTP 423 `WEBSOCKET_NOT_FRESH` |

Wallet exposure was approximately 94.8%, above configured
`max_total_exposure_pct=90%`. Health therefore also listed
`MAX_TOTAL_EXPOSURE_REACHED`. This correctly blocks ARM despite managed positions
being zero: wallet exposure and Agent-managed position slots remain separate.

## 10. Backtest evidence

A real read-only `BTC-USDT --days 7` run completed against paginated Demo MCP
historical OHLCV:

| Timeframe | Bars | Duplicates | Missing | Unconfirmed | Integrity |
|---|---:|---:|---:|---:|---|
| 1m | 10,080 | 0 | 0 | 0 | PASS |
| 3m | 3,360 | 0 | 0 | 0 | PASS |
| 5m | 2,016 | 0 | 0 | 0 | PASS |

The run produced 42 simulated trades using production strategy/risk/sizing,
next-bar-open execution and explicit FixedFee/FixedSpread/FixedSlippage models.
The result is an engineering snapshot, not evidence of future profitability.

## 11. Test and build matrix

| Check | Result |
|---|---|
| Python full suite | 73 passed, 0 failed |
| Dashboard/API safety test module | 12 passed |
| Frontend Vitest | 5 passed, 0 failed |
| TypeScript no-emit checks | PASS |
| Vite production build | PASS, 30 modules transformed |
| Python compileall | PASS |
| Ruff on new service/Web files | PASS with intentional broad fail-closed catches ignored |
| npm official-registry audit | 0 vulnerabilities |
| `git diff --check` | PASS |
| Real API startup/root/session/read routes | PASS |
| Real WebSocket upgrade/heartbeat | PASS |
| Real Demo order submission | NOT PERFORMED |

Tests cover startup DISARM, AUTO reset, mode/start/arm/disarm, exact AUTO and Kill
reset phrases, Kill/ARM concurrency, protected-position preservation, AUTO plan
ID semantics, disconnect stopping AUTO/final execution, runtime failure
degradation, session/CSRF/Host/CORS/CSP, WebSocket freshness, multi-tab stream
tracking, Live lock, approval request schema, OpenAPI paths and sanitization.
The pre-existing Core suite continues to cover duplicate approval/submission,
TTL, movement/staleness/spread, risk, idempotency, uncertain submission,
recovery, partial fills and protection failure.

## 12. Files changed or added

Core/service/backend:

- `.gitignore`, `pyproject.toml`, `uv.lock`
- `storage/database.py`, `storage/control_store.py`, `storage/signal_store.py`,
  `storage/trade_store.py`
- `trading_agent/control_state.py`, `trading_agent/service.py`,
  `trading_agent/control_api.py`, `trading_agent/orchestrator.py`,
  `trading_agent/state.py`, `trading_agent/web_server.py`
- `trading_agent/web_api/{app.py,schemas.py,security.py}`

Frontend:

- `frontend/package.json`, `frontend/package-lock.json`, TypeScript/Vite configs
- `frontend/index.html`, `frontend/public/favicon.svg`
- `frontend/src/{main.tsx,App.tsx,api.ts,types.ts,styles.css}`
- `frontend/src/{App.test.tsx,api.test.ts,test-setup.ts}`

Tests/docs:

- `tests/test_web_service.py`
- `README.md`, `docs/architecture.md`, `docs/commands.md`
- `docs/web-dashboard-compatibility.md`, `docs/web-dashboard-api-v1.md`
- this audit

## 13. Self-review findings fixed

1. **CSRF missing-header acceptance** — session validation initially treated a
   missing CSRF value as read-only validation. Write dependency now explicitly
   rejects an empty header; regression test added.
2. **TrustedHost invalid wildcard** — unsupported port wildcards failed app
   startup. Host patterns were reduced to valid exact localhost names.
3. **Control transition race** — ARM and Kill could interleave. Serialized action
   lock and compensating safe reset added.
4. **Kill/AUTO race** — Kill state is now persisted before Core synchronization;
   final entry guard reads service control state.
5. **WebSocket only protected at UI/API** — AUTO could have continued after the
   browser disconnected. Service and DemoExecutor freshness gates added.
6. **Missing production WebSocket driver** — real Chrome exposed an unsupported
   upgrade. Locked `websockets` runtime dependency added and real upgrade passed.
7. **Slow blank first load** — UI had awaited every serialized MCP read before
   connecting. It now connects first, uses a cache/SQLite WS snapshot and fills
   panels progressively.
8. **Refresh feedback loop** — health/account/scanner events previously caused a
   read that emitted the same event. Frontend now applies those events directly.
9. **Bearer redaction gap** — authorization redaction could leave a trailing
   bearer credential. Full authorization-line and bearer patterns added.
10. **Generated TypeScript artifacts** — no-emit build commands and ignore rules
    prevent config JS/type-build files entering source control.
11. **Mobile horizontal overflow** — root/safety/nav/main min-width boundaries
    were tightened after 390px visual inspection.

## 14. Known limitations and remaining work

- Live is intentionally `LOCKED / NOT IMPLEMENTED`; W8 was not started.
- Real Demo execution was not performed because no order authorization was given
  and current wallet exposure independently blocks ARM.
- AUTO requires at least one connected local Dashboard control stream. This is
  intentional W0-W7 fail-closed behavior, not unattended server automation.
- Session and WS event history are in memory and process-local. The supported
  deployment is one process/worker; horizontal scaling is unsupported.
- WebSocket keeps the latest 500 process events. Reconnect relies on a fresh
  snapshot rather than replay across backend restart.
- Backtest cost models remain configurable fixed models; empirical Demo
  slippage is future work after authorized execution data exists.
- Settings intentionally do not permit browser edits to risk limits, symbols,
  credentials or execution backend.
- No scheduler daemon, cloud access, remote access, React router, native push,
  macOS App or ML is included.

## 15. Final readiness

**LOCAL WEB DASHBOARD W0–W7: WORKING**

**READY FOR LONG-TERM LOCAL DEMO CONTROL SUBJECT TO CURRENT RISK ELIGIBILITY**

**READY FOR FIRST CONTROLLED DEMO ORDER: BLOCKED BY CURRENT WALLET EXPOSURE AND
STILL REQUIRES EXPLICIT USER APPROVAL**

**LIVE: LOCKED / NOT IMPLEMENTED**
