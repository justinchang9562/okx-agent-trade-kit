# Post-Implementation Remediation v0.3.0

## Scope and safety boundary

This audit log records the local implementation of the mandatory P0-P8 remediation and the P9
research framework from `OKX_Agent_Trade_Kit_Full_Audit_Remediation_Report_v0.3.0.pdf`.

- Baseline branch: `feature/local-web-dashboard-v2.1`
- Baseline Core commit: `2f3c8d234380c539ce1155af2b633dea14b57832`
- Remediation branch: `audit/remediation-v0.3.0`
- Preserved user-work checkpoint: `7772ae6`
- Release version: `0.3.0`
- Dashboard workstream name: `Local Web Dashboard v2.1`
- Environment boundary: Demo only; Live remains locked and not implemented.
- Real Demo order submission during remediation: **NOT PERFORMED**.

No phase may bypass `RiskManager`, accept browser-owned order parameters, remove TP/SL protection,
or treat unavailable data as permission to continue.

## P0 - Baseline and test guardrails

### Environment

- Date: 2026-08-23 Asia/Taipei
- Python: 3.13.7
- Node.js: 24.15.0
- npm: 11.12.1
- Baseline Python project version: 0.2.1
- Baseline frontend package version: 2.1.0

### Preserved pre-existing work

The initial worktree contained uncommitted localization and Dashboard changes in `App.tsx`,
`App.test.tsx`, `styles.css`, and a new `locales.ts`. Work stopped as required, user approval was
obtained, and the changes were preserved in checkpoint commit `7772ae6` before remediation began.

### Baseline commands and results

| Command | Result |
| --- | --- |
| `uv sync --extra dev` | PASS |
| `uv run pytest -m 'not integration'` | PASS - 72 passed, 1 deselected |
| `uv run python -m compileall trading_agent data indicators strategies risk decision execution backtest storage monitoring` | PASS |
| `cd frontend && npm ci` | PASS |
| `cd frontend && npm test -- --run` | PASS - 10 tests |
| `cd frontend && npm run build` | PASS |

### Changes

- Unified the Python and frontend release version at `0.3.0` while retaining Dashboard v2.1 as a
  workstream/product label.
- Added `ruff` to the declared development toolchain.
- Created this phase-by-phase evidence log.

### Security impact

No execution behavior changed in P0. Demo-only and Live-locked defaults remain in force.

## P1 - Controlled real Demo lifecycle harness

### Changed files

- `trading_agent/demo_lifecycle.py`, `scripts/verify_demo_lifecycle.py`
- `risk/position_sizing.py`
- `tests/test_demo_lifecycle_harness.py`, `tests/integration/test_real_demo_lifecycle.py`

### Rationale and behavior

The verifier defaults to `PRECHECK_READ_ONLY` and reports health, verified Demo identity, MCP
capabilities, account, market, spread, instrument size/tick rules, risk-owned plan preview,
minimum executable quantity, estimated notional and a state timeline. It exports only the
sanitized preview. Submission requires the exact `--submit-real-demo` flag and exact phrase,
the configured Demo environment, an available verified Demo backend, attached TP/SL support and
a passing preview. The minimum executable quantity only caps an already approved position; it
cannot increase size or bypass Risk Manager. Resume mode reconciles persisted order/position state
without resubmission or automatic cancellation.

### Tests and evidence

- Harness gate, read-only precheck, lot-aligned minimum size and resume-only reconciliation:
  `tests/test_demo_lifecycle_harness.py`.
- Submission uncertainty/no retry, restart recovery and missing protection:
  `tests/test_core_hardening.py`.
- Exit closure, partial fills and fee evidence: `tests/test_reconciliation_pagination.py`.
- Real integration marker is excluded from normal CI.

### Known limitations and security impact

No real Demo order was submitted during remediation. Therefore exchange-observed OPEN/FILLED,
attached TP/SL and terminal fee/PnL evidence remain intentionally unclaimed. The harness makes a
future user-approved test possible without weakening any normal execution guard.

## P2 - Backtest/runtime resource isolation

### Changed files

- `trading_agent/backtest_service.py`, `trading_agent/service.py`, `trading_agent/cli.py`
- `execution/read_only_backend.py`, `execution/mcp_backend.py`
- `tests/test_backtest_isolation.py`

### Rationale and behavior

Backtests use a dedicated single-thread worker and a distinct write-disabled backend that launches
the public read-only MCP market module without credentials. They never reuse OrderManager,
DemoExecutor or the authoritative Core worker. ARMED execution returns
`BACKTEST_BLOCKED_WHILE_EXECUTION_ARMED`; an active runtime without explicit concurrent read
capability returns `BACKTEST_BACKEND_CONCURRENCY_UNAVAILABLE`. Shutdown closes both worker/backend
resources.

### Tests, limitations and security impact

Deterministic slow-run tests prove status and Kill Switch paths return while a backtest is blocked,
and verify ARMED/runtime fail-closed gates. No external rate-limit concurrency is claimed beyond
capability detection. The read-only wrapper raises on every write attempt.

## P3 - Fill history and long-stop reconciliation

### Changed files

- `execution/base_backend.py`, `execution/mcp_backend.py`, `execution/order_manager.py`,
  `execution/order_state.py`
- `storage/database.py`, `storage/trade_store.py`
- `tests/test_reconciliation_pagination.py`

### Rationale and behavior

The installed `spot_get_fills` MCP schema was inspected before implementation and confirms
`ordId`, `after`, `before`, `begin`, `end`, `archive` and `limit`. Recovery now prefers exact
protective-order lookup and otherwise uses bounded pagination: at most 20 pages, recent three-day
history, or archive history up to 90 days. Fill IDs are deduplicated; partial/multiple exits and
fee currencies are aggregated. Cursor, normalized exit-fill facts and structured audit tables were
added at database schema version 5. Cursor streams are isolated per plan and participate in the
next query window; the normalized ledger preserves cumulative partial quantity/fees without
storing raw exchange responses. Incomplete history returns `EXIT_FILL_HISTORY_INCOMPLETE` or
`RECOVERY_DATA_INSUFFICIENT` and leaves the position open.

### Tests, limitations and security impact

Tests cover a target on page three, duplicate fills, partial exit, incomplete history and repeated
restart idempotency. Fee amounts in different currencies are retained separately and are not
invented into USDT PnL; negative OKX fee values are normalized to positive costs, while only quote-
currency fees enter PnL and conversion-required status remains explicit. Raw authorization data is not
persisted in reconciliation audit rows.

## P4 - Executable-price risk revalidation

### Changed files

- `risk/execution_revalidation.py`, `trading_agent/orchestrator.py`
- `backtest/backtester.py`, `backtest/performance.py`
- `tests/test_execution_revalidation.py`

### Rationale and behavior

Production approval and backtesting share a pure helper that quantizes executable entry, stop and
target, recomputes RR, reruns Risk Manager and recalculates risk-sized quantity. Backtests preserve
no-lookahead by building the signal only from completed candles, then use only next-bar open plus
configured spread/slippage for execution revalidation. Strategy is not rerun on the execution bar.
The conservative stop-first same-bar rule and all costs remain. Results include skipped counts,
reason breakdown, assumptions, equity curve and drawdown curve.

### Tests, limitations and security impact

Gap/open stress tests verify recalculated quantity/RR and fail-closed skips. Historical simulation
remains a model, not evidence of future profitability; the output exposes its fixed-cost assumptions.

## P5 - Client-acknowledged WebSocket freshness

### Changed files

- `trading_agent/web_api/security.py`, `trading_agent/web_api/app.py`
- `trading_agent/service.py`, `frontend/src/App.tsx`
- `tests/test_web_service.py`

### Rationale and behavior

Freshness now means a recent authenticated client ACK, never a successful server send. The only
accepted client message is strict `{type: "heartbeat.ack", sequence: integer}`; malformed messages
close the socket and cannot reach any control method. Any one fresh tab qualifies, while all stale
tabs fail high-risk writes and final entry guards after eight seconds.

### Tests, limitations and security impact

Tests cover connected-without-ACK, one-of-two tabs fresh, all tabs stale, malformed input and
reconnect. WebSocket remains incapable of mode/order/approval commands.

## P6 - One-click, one-time Demo approval challenge

### Changed files

- `trading_agent/web_api/security.py`, `trading_agent/web_api/schemas.py`,
  `trading_agent/web_api/app.py`, `trading_agent/service.py`, `trading_agent/orchestrator.py`
- `frontend/src/App.tsx`, `frontend/src/types.ts`, `frontend/src/locales.ts`
- `tests/test_approval_challenges.py`, `tests/test_web_service.py`

### Rationale and behavior

A successful preview creates a 15-second random challenge bound to session cookie, plan ID and a
SHA-256 revision of the server-owned preview. Final approval accepts only that token, consumes it
once, reloads fresh market/account data and reruns Core revalidation. Material entry price, size or
risk drift returns `APPROVAL_PREVIEW_CHANGED` and requires a new preview. Plan-state CAS and stable
client order ID retain idempotency. CLI exact-phrase approval, AUTO enable phrase and Kill reset
phrase are unchanged.

### Tests, limitations and security impact

Tests cover session/plan binding, expiry, one-use consumption and price-change rejection without
submission. Challenges are intentionally process-memory-only and become invalid after restart.

## P7 - CI, release and public repository safety

### Changed files

- `.github/workflows/ci.yml`, `scripts/check_repo_safety.py`
- `pyproject.toml`, `uv.lock`, `frontend/package.json`, `frontend/package-lock.json`
- `SECURITY.md`, `CHANGELOG.md`, `docs/release-checklist.md`, `README.md`

### Rationale and behavior

CI defines Python 3.11/3.12 lint, non-integration tests and compile checks; Node 22 install, tests
and production build; and a static tracked-artifact/secret-pattern guard. CI contains no OKX
credentials and excludes real integration tests. Runtime DB, logs, cache, build and audit exports
remain ignored. Project and frontend release versions are `0.3.0`; Dashboard v2.1 remains a product
workstream name, not the package version.

### Tests, limitations and security impact

The workflow-equivalent commands and safety script are run locally in the final gate. The initial
local pass does not claim a GitHub Actions cloud result. The user subsequently authorized commit
and push; remote synchronization and any cloud workflow state are verified separately at handoff.

## P8 - Observable, fail-closed Dashboard controls

### Changed files

- `storage/control_store.py`, `trading_agent/service.py`, `trading_agent/orchestrator.py`
- `frontend/src/App.tsx`, `frontend/src/styles.css`, `frontend/src/types.ts`,
  `frontend/src/locales.ts`
- `tests/test_web_service.py`

### Rationale and behavior

Status adds client ACK age, last scan/health, nearest plan expiry, wallet exposure percentage,
managed exposure, daily PnL and consecutive losses. Orders render a lifecycle rail/table with
critical unknown/unprotected warnings; positions separate wallet assets and Agent-managed rows;
backtests show the required metrics, assumptions, skip reasons, equity/drawdown SVGs and collapsed
raw detail; approvals show freshness, challenge countdown, price/size/risk/notional/RR/stop/target.
Kill state is always visible, ARMED is prominent and Live remains disabled/locked. Mobile controls
remain visible at 390 px.

Startup preserves a valid 5-3600 second scan interval and, conservatively, an ACTIVE Kill Switch.
It always resets execution to DISARMED, runtime/mode to STOPPED and AUTO off.

### Tests, limitations and security impact

State persistence and fail-closed controls have unit/API coverage. Desktop and 390 px browser
verification results are recorded in the final verification section below. The frontend computes no
strategy, risk, sizing or execution values; it only presents server results.

## P9 - Research framework without production promotion

### Changed files

- `config/experiments.yaml`, `config/trading_rules.yaml`
- `strategies/registry.py`, `strategies/signal.py`
- `backtest/regimes.py`, `scripts/run_research_evaluation.py`
- `tests/test_research_framework.py`

### Rationale and behavior

Production is frozen at `scalping_v1_baseline`; production construction rejects the experimental
`scalping_v1_quality_research`. The research version adds independent trend/volume/volatility/
spread/structure metadata without altering the baseline signal gate. The batch command compares
baseline plus registered research candidates across BTC/ETH/SOL x 7/30/90 days x base/1.5x/2x
costs plus rolling OOS output, per-run evidence, per-strategy/scenario aggregate metrics and
realized-range quantile regime buckets. Promotion is explicitly manual and
requires three symbols, multiple OOS windows, drawdown/PF/fees/trade-count evidence.

### Tests, limitations and security impact

Tests verify production rejection, baseline equivalence, objective buckets, evaluation matrix and
aggregate output. The network-backed research matrix was not run as part of deterministic
remediation, so no alpha or profitability claim is made and baseline parameters remain unchanged.

## Final verification

### Branch and working tree

- Current branch: `audit/remediation-v0.3.0`
- Baseline commit: `2f3c8d234380c539ce1155af2b633dea14b57832`
- Preserved user-work checkpoint: `7772ae6f11c1a65bd34dea77ba162437c20d16c7`
- Implementation commit: `b744a8cbc86d3b87eaa87b3a38b54c79ff9ad2c1`
- Audit evidence: finalized by this document's containing commit for authorized GitHub sync.

### Final local command matrix

| Command | Result |
| --- | --- |
| `uv sync --extra dev` | PASS - 30 resolved, 28 checked |
| `uv run ruff check .` | PASS |
| `uv run pytest -m "not integration"` | PASS - 103 passed, 2 deselected, 1 deprecation warning |
| `python -m compileall ...` | Environment alias unavailable: `python: command not found` |
| `uv run python -m compileall ...` | PASS |
| `python3 -m compileall ...` | PASS |
| `cd frontend && npm ci` | PASS - 167 packages installed from lockfile |
| `cd frontend && npm test -- --run` | PASS - 10 tests |
| `cd frontend && npm run build` | PASS - TypeScript and Vite production build |
| `uv run python scripts/check_repo_safety.py` | PASS |
| CI workflow YAML parse | PASS - 3 jobs |
| SQLite migration smoke test | PASS - schema version 5 and normalized exit-fill table present |
| `git diff --check` | PASS |

The warning is a Starlette/httpx test-client deprecation notice and did not fail any test. The
project-supported `uv run python` compile command passed; the missing shell alias is recorded rather
than hidden.

### Browser evidence

`agent-browser 0.34.0` verified the locally served production bundle at desktop 1440x1000 and
mobile 390x844. The page had meaningful content, no Vite/Next/Webpack error overlay, no page errors
or console errors, and Orders, Positions, Backtest and Settings navigation rendered their expected
structured headings. Mobile `body.scrollWidth` equalled 390; Disarm and Kill buttons were visible,
enabled in the safe DISARMED state, and were not clicked. The verification caused one UI correction:
mobile now retains explicit Execution and Kill Switch labels. Screenshots were kept outside the
repository because the read-only Demo account projection can contain account values.

### P0-P9 disposition

| Phase | Status | Evidence boundary |
| --- | --- | --- |
| P0 | DONE | Branch, checkpoint, version and baseline recorded. |
| P1 | DONE (harness) | Default-read-only harness and deterministic lifecycle tests pass; real Demo order pending user approval. |
| P2 | DONE | Dedicated worker/read-only backend and non-blocking tests pass. |
| P3 | DONE | Exact/capability-aware bounded recovery, cursor/ledger and idempotency tests pass. |
| P4 | DONE | Shared executable-price revalidation and gap tests pass. |
| P5 | DONE | Client ACK freshness and multi-tab/malformed-message tests pass. |
| P6 | DONE | Single-use bound challenge, re-preview and duplicate-browser-submit tests pass. |
| P7 | DONE (local) | Workflow and local equivalents pass; cloud run state is verified after push. |
| P8 | DONE | Structured UI, persistent conservative Kill policy and desktop/mobile browser checks pass. |
| P9 | DONE (framework) | Comparison framework/tests pass; network research matrix and strategy promotion were not run. |

### Non-claims and remaining external evidence

- Real Demo order submission: **NOT PERFORMED**. Exchange-observed full lifecycle remains pending
  separate user approval.
- GitHub Actions cloud run: not inferred from local validation; post-push status is reported in the
  final handoff.
- Network-backed multi-symbol/OOS research matrix: **NOT PERFORMED**; no alpha, optimization or
  profitability claim is made.
- Live backend: **LOCKED / NOT IMPLEMENTED**.
