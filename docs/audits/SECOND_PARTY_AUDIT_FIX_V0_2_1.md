# Core Hardening v0.2.1 — Second-Party Audit Fix

Audit date: 2026-08-22 (Asia/Taipei)

Project version: 0.2.1

Baseline commit: `4e1eb7012a6fb01c45d6a0b65a12009bd62ba755` (`Core Trading Agent v0.2 hardening`)

Working branch: `fix/core-hardening-v0.2.1`

Scope: execution safety, state integrity, managed-position lifecycle, concurrency, health semantics, migration and auditability

## 1. Executive Summary

The v0.2 baseline was preserved on a dedicated branch and its original test suite
was run before modification: **43 passed, 0 failed**. The second-party findings
were then independently reproduced against the current source rather than accepted
from the earlier audit report.

Core v0.2.1 adds a hard runtime stop at both orchestration and final execution
boundaries; rebuilds the approved plan from a single final executable-price basis;
uses Decimal tick-size quantization and final risk/reward validation; reserves
position slots and notional before fill; introduces atomic order-state CAS; closes
managed position, trade and lifecycle records together; classifies known pre-submit
failure separately from uncertain submission; fails closed on material unpriced
wallet assets; and separates system capability from current trading eligibility.

The current deterministic suite is **61 passed, 0 failed**. Real OKX Demo checks
were read-only. No place, amend, cancel, protective-order creation or Live operation
was invoked. Live remains **LOCKED_NOT_IMPLEMENTED**.

## 2. Audit Method and Safety Boundary

The review covered the repository instructions, configuration, orchestration,
risk/exposure/sizing, execution backends and state machine, SQLite persistence,
health reporting, control API, and tests. Production strategy parameters and
`rule_scalping_v1` logic were not modified.

The permitted real-Demo evidence consisted only of market data, instrument metadata,
account/balance, open-order, fill and protection capability queries. Approval was
tested only with deterministic backends. The exact-confirmation CLI path was never
called against the real backend.

## 3. Files Changed

Configuration and metadata:

- `config/trading_rules.yaml` — configurable unpriced-asset dust quantity.
- `pyproject.toml`, `uv.lock`, `trading_agent/__init__.py` — version 0.2.1.
- `README.md` — v0.2.1 safety and readiness semantics.

Core and execution:

- `trading_agent/state.py` — new-entry permission semantics.
- `trading_agent/orchestrator.py` — STOPPED gate, final-price rebuilding, reservations,
  exposure and eligibility checks.
- `trading_agent/cli.py` — health exit status follows system capability.
- `execution/errors.py` — classified execution and state-race errors.
- `execution/demo_executor.py` — final runtime and Demo/protection gates.
- `execution/mcp_backend.py` — serialized requests, bounded locking, safe stderr drain,
  placement-failure classification and version.
- `execution/order_manager.py` — classified submission outcomes, protective-ID linking,
  entry/exit reconciliation and recovery.
- `risk/price_quantization.py` — Decimal tick-size rounding and final RR calculation.
- `risk/exposure.py` — reserved notional, exposure-known status and dust handling.
- `risk/risk_manager.py` — final RR reason and fail-closed unknown exposure.
- `monitoring/health.py` — capability claims based on observed runtime checks and
  non-overstated schema readiness.

Persistence:

- `storage/database.py` — schema version 2, migrations, new exit/protection linkage,
  WAL/busy timeout and cross-thread connection configuration.
- `storage/trade_store.py` — repository locking, CAS transitions, reserved slots/notional,
  managed close/PnL transaction and linkage fields.
- `storage/signal_store.py` — serialized shared-connection writes.

Tests and documentation:

- `tests/test_second_party_audit_v021.py` — v0.2.1 regression/fault/concurrency tests.
- `tests/test_core_hardening.py` — genuine process-crash parameter use and classified
  uncertain failures.
- `tests/test_risk_manager.py` — final RR reason semantics.
- `docs/architecture.md`, `docs/risk-management.md`, `docs/execution-backends.md` —
  concurrency, reservations, failure classification and lifecycle documentation.
- This report.

## 4. Second-Party Findings and Fix Status

### Finding 1 — STOPPED execution bypass

- Original problem: `stop_trading()` changed display state, while `approve_plan()` and
  the final executor did not enforce it.
- Severity: **P0 / Critical**.
- Root cause: runtime state was not an execution invariant.
- Files: `trading_agent/state.py`, `trading_agent/orchestrator.py`,
  `execution/demo_executor.py`.
- Fix: new entries require `AgentState.allows_new_entries`; approval rejects with
  `TRADING_STOPPED`; DemoExecutor checks immediately at entry and again immediately
  before `place_order`. Existing protective state is untouched.
- Tests: stopped approval, final executor call count zero, protection state retained.
- Status: **WORKING**. Runtime mode remains process-local; restart uses the configured
  default, which is manual approval with AUTO_DEMO disabled.

### Finding 2 — Final executable price did not drive all risk fields

- Original problem: entry changed from last price to ask, while stop/TP, sizing,
  risk amount and RR could remain based on the old entry.
- Severity: **P0 / Critical**.
- Root cause: approval reused preliminary signal/sizing output after changing entry.
- Files: `trading_agent/orchestrator.py`, `risk/risk_manager.py`.
- Fix: approval fetches fresh data, determines final ask, quantizes all prices,
  calculates final RR, sizes from final entry-to-stop distance, rebuilds exposure,
  reruns RiskManager, and persists/submits only the rebuilt final TradePlan.
- Tests: ask degradation below minimum RR rejects; final risk amount equals final
  quantity times final entry-stop distance.
- Status: **WORKING**.

### Finding 3 — Stop/TP did not respect OKX tickSz

- Original problem: arbitrary floating-point protection prices could reach execution.
- Severity: **P0 / High**.
- Root cause: lot-size quantization existed but price tick quantization did not.
- Files: `risk/price_quantization.py`, `trading_agent/orchestrator.py`.
- Fix: Decimal division/multiplication implements conservative Long rounding: entry
  and stop round upward, take-profit rounds downward. Ordering and RR are revalidated
  after rounding.
- Tests: 0.1 tick precision, safe ordering, post-quantization RR failure.
- Status: **WORKING**.

### Finding 4 — ManagedPosition could remain active after exit

- Original problem: trade PnL could close while `managed_positions` stayed FILLED,
  permanently occupying a slot.
- Severity: **P0 / Critical**.
- Root cause: `close_trade()` updated only the trade row and lacked durable exit links.
- Files: `storage/database.py`, `storage/trade_store.py`, `execution/order_manager.py`.
- Fix: entry, exit and protective IDs are persisted. A linked complete protective
  sell fill atomically finalizes fees/PnL, marks the ManagedPosition CLOSED, marks
  entry lifecycle CLOSED, stores the exit order ID and releases the slot. Partial
  exit fills do not claim closure.
- Tests: direct entry-to-exit lifecycle, linked protective-fill reconciliation,
  finalized PnL and slot release.
- Status: **WORKING** for the domain/persistence and deterministic reconciliation;
  **PARTIALLY_WORKING** for real Demo because an attached TP/SL fill was intentionally
  not created during this no-side-effect audit.

### Finding 5 — Pending entries did not reserve capacity

- Original problem: multiple unfilled entries could all see zero managed positions
  and exceed position/exposure limits.
- Severity: **P0 / Critical**.
- Root cause: only filled managed rows contributed to the open count.
- Files: `storage/trade_store.py`, `risk/exposure.py`, `trading_agent/orchestrator.py`.
- Fix: `position_slots_in_use()` unions and deduplicates managed positions with active
  entry lifecycle states. `reserved_entry_notional()` values the unfilled remainder.
  Both feed fresh approval risk and health.
- Tests: two reserved entries consume two slots/notional and the third is rejected
  with `MAX_OPEN_POSITIONS_REACHED`.
- Status: **WORKING**.

### Finding 6 — Non-atomic order transitions

- Original problem: separate SELECT/UPDATE allowed a stale worker to overwrite a
  newer state and move FILLED backward to OPEN.
- Severity: **P0 / Critical**.
- Root cause: UPDATE matched only `plan_id`.
- Files: `storage/trade_store.py`, `execution/errors.py`.
- Fix: every lifecycle transition validates the allowed edge and performs
  `UPDATE ... WHERE plan_id=? AND state=?`; a zero row count raises
  `ORDER_STATE_CHANGED` and prevents the plan row from being changed.
- Tests: stale two-connection CAS and simultaneous two-thread CAS; exactly one wins;
  illegal backward transition fails.
- Status: **WORKING**.

### Finding 7 — All executor exceptions became SUBMISSION_UNKNOWN

- Original problem: local guards and explicit exchange rejection were mislabeled as
  possibly accepted orders.
- Severity: **P1 / High**.
- Root cause: one broad exception handler surrounded execution.
- Files: `execution/errors.py`, `execution/demo_executor.py`,
  `execution/mcp_backend.py`, `execution/order_manager.py`.
- Fix: `PreSubmitRejectedError` produces REJECTED with `PRE_SUBMIT_REJECTED`;
  explicit MCP tool errors are known rejection; only timeout/crash/response-loss
  paths represented by `SubmissionUncertainError` become SUBMISSION_UNKNOWN and
  require reconciliation. There is no blind placement retry.
- Tests: local guard rejection, before-timeout, actual process-crash mode, response
  loss after acceptance and duplicate retry block.
- Status: **WORKING**.

### Finding 8 — Material unpriced wallet assets were valued as zero

- Original problem: missing prices were listed but omitted from exposure while risk
  could still pass.
- Severity: **P1 / High**, especially for future Live.
- Root cause: exposure had no known/unknown state.
- Files: `config/trading_rules.yaml`, `risk/exposure.py`, `risk/risk_manager.py`.
- Fix: material missing-price assets set `status=UNKNOWN`; RiskManager rejects with
  `EXPOSURE_UNKNOWN`. Only balances at or below the configured quantity dust limit
  are ignored and reported separately. No value is invented.
- Tests: material XYZ fails closed; sub-threshold dust is ignored.
- Status: **WORKING**.

### Finding 9 — Health conflated capability with eligibility

- Original problem: table existence was reported as feature PASS and current 94%+
  exposure could coexist with a broad READY statement.
- Severity: **P1 / High**.
- Root cause: static capability and live risk state shared one readiness label.
- Files: `monitoring/health.py`, `trading_agent/orchestrator.py`, `trading_agent/cli.py`.
- Fix: health now reports `system_capability` separately from
  `trading_eligibility`. Eligibility evaluates runtime, data/spread, wallet and
  reserved exposure, daily/consecutive loss, slots, unknown submissions and
  unprotected positions. Table checks are labeled SCHEMA_READY, not full behavior PASS.
- Tests: system READY plus excessive wallet exposure correctly returns eligibility NO.
- Status: **WORKING**.

### Finding 10 — process_crash test did not use its parameter

- Original problem: both parameter cases instantiated `before_timeout`; process crash
  was not actually covered.
- Severity: **P1 / Medium**.
- Root cause: a hard-coded fixture argument.
- Files: `tests/test_core_hardening.py`.
- Fix: backend receives `mode`; crash raises the dedicated uncertain-submission error.
- Tests: both before-timeout and process-crash cases run independently.
- Status: **WORKING**.

### Finding 11 — Shared MCP stdio concurrency risk

- Original problem: concurrent writers/readers could interleave JSON-RPC messages or
  consume another request's response.
- Severity: **P1/P2 / High for future Web**.
- Root cause: no request serialization or dispatcher.
- Files: `execution/mcp_backend.py`.
- Fix: one bounded lock encloses request ID allocation, write and matching response
  read. Notification writes use the same lock. Lock acquisition times out fail-closed.
- Tests: four concurrent calls demonstrate maximum one in-flight read and ordered
  unique request IDs.
- Status: **WORKING** as the single-connection foundation; no Web server was added.

### Finding 12 — Shared SQLite connection concurrency risk

- Original problem: future concurrent control calls could share unprotected repository
  connections and transaction state.
- Severity: **P1/P2 / High for future Web**.
- Root cause: CLI-only assumptions in repository design.
- Files: `storage/database.py`, `storage/trade_store.py`, `storage/signal_store.py`.
- Fix: WAL, FULL synchronous mode, 10-second busy timeout, cross-thread connection
  option, per-repository reentrant locks and explicit immediate transaction boundaries.
  Cross-connection correctness depends on SQLite plus state CAS.
- Tests: simultaneous independent-connection state writers; migration/data-preservation.
- Status: **WORKING** core foundation. Future Web must call `LocalControlAPI` and must
  not access repository or MCP internals directly.

### Finding 13 — Version inconsistency

- Original problem: project/package/MCP metadata contained 0.1.0 or 0.2.0 while the
  release was called v0.2.
- Severity: **P2 / Medium**.
- Root cause: no coordinated version update.
- Files: `pyproject.toml`, `uv.lock`, `trading_agent/__init__.py`,
  `execution/mcp_backend.py`, `README.md`.
- Fix: all project-controlled version surfaces are 0.2.1.
- Tests: package version assertion and runtime import output.
- Status: **WORKING**.

### Finding 14 — No explicit SQLite schema version

- Original problem: additive changes had no durable current-version record.
- Severity: **P2 / Medium**.
- Root cause: ad hoc `_ensure_column` only.
- Files: `storage/database.py`.
- Fix: `schema_migrations(version, applied_at_ms)`, schema version 2 and idempotent
  additive migration for exit/protection linkage and existing compatibility columns.
- Tests: a v0.2-style database migrates to version 2 without losing its signal row.
- Status: **WORKING** lightweight migration framework.

### Finding 15 — MCP stderr was not continuously drained

- Original problem: a long-lived stderr pipe could fill and block the subprocess;
  reading raw stderr during failure could also disclose sensitive content.
- Severity: **P2 / Medium**.
- Root cause: stderr was read only after process exit.
- Files: `execution/mcp_backend.py`.
- Fix: daemon drain thread consumes and discards content, retaining only a line count.
  MCP/tool error rendering no longer includes raw content or structured payloads.
- Tests: a simulated tool error containing a credential-like string does not echo it.
- Status: **WORKING**.

## 5. Managed Position and Exposure Semantics

The following concepts are now deliberately separate:

- Wallet balances are physical account inventory and contribute to wallet exposure.
- Managed positions are filled quantities created and tracked by this Agent.
- Reserved entries are approved/submitted/unknown/open/partial entry quantities not
  yet represented fully in the wallet.
- Position slots are the union of managed positions and reserved entry lifecycles,
  deduplicated by `plan_id`.
- Effective projected exposure is wallet exposure plus reserved remaining notional
  plus the proposed new order. Managed notional is reported separately and is not
  double-counted when already present in the spot wallet.

The real Demo account proved the distinction: BTC and ETH were both non-zero, but
managed positions and slots remained zero. The trade was not blocked by
`MAX_OPEN_POSITIONS_REACHED`; current eligibility was blocked independently by the
configured total-exposure cap.

## 6. Approval and Final Execution Flow

```text
Approve existing plan_id
  -> runtime STOPPED guard
  -> plan exists / PLANNED / TTL
  -> fresh market and account
  -> fresh rule_scalping_v1 signal
  -> final executable ask
  -> plan-entry deviation guard
  -> Decimal entry/stop/TP tick quantization
  -> final RR calculation
  -> daily/consecutive/slot/duplicate/spread checks
  -> final position sizing and risk amount
  -> wallet + reserved + proposed exposure
  -> final RiskManager veto
  -> pre-submit slippage guard
  -> execution preview
  -> exact confirmation
  -> atomic approval/order reservation
  -> persisted SUBMITTED
  -> final Demo/runtime/protection-capability guard
  -> backend placement
  -> acknowledgement or classified reconciliation
```

The external approve API accepts only `plan_id` and exact confirmation. It does not
accept symbol/side/amount from a UI.

## 7. State, Idempotency and Recovery

Order lifecycle retains plan/client/OKX IDs, requested/filled size, average fill,
expected price, slippage, actual-or-unknown fee, timestamps, backend/environment,
protection and classified error. The unique plan row and client-order ID are the
idempotency boundary. A may-have-reached-OKX failure remains reserved as
SUBMISSION_UNKNOWN, is queried by client ID, and is never blindly placed again.

Restart recovery loads active lifecycle rows, reconciles their exchange state, then
checks managed positions for sell fills linked to persisted protective IDs. Entry
and linked exit closure is a single SQLite transaction.

## 8. Test Matrix

Baseline before modification: **43 passed, 0 failed**.

Final deterministic suite: **61 passed, 0 failed**.

Static import/bytecode compile: **PASS**.

The suite covers:

- STOPPED approval/final-executor gates and retained protection.
- Final executable price, post-tick RR, risk amount and Decimal precision.
- Wallet assets versus managed positions.
- Two reserved slots blocking a third and reserved notional.
- Partial entry fill and partial exit not claiming completion.
- Managed CLOSED/PnL/fees/ID linkage/slot release.
- Duplicate approval/submission and exact approval.
- Before-submit uncertainty, process crash, accepted-response loss and recovery.
- Known local rejection versus SUBMISSION_UNKNOWN.
- Restart with active order, cancel rejection and unprotected position.
- Missing actual fill fee remains NULL/UNKNOWN.
- Expired plan, moved price, stale market, expanded spread and daily kill switch.
- Material unpriced asset and dust behavior.
- CAS/backward-state/concurrent writer safety.
- MCP request serialization and credential-safe error handling.
- Schema migration/version preservation and component version.
- Capability versus eligibility health semantics.

## 9. Real OKX Demo Read-Only Evidence

Evidence time: 2026-08-22, during this audit. Values are observations returned by
the configured Demo MCP and are not fabricated.

- MCP backend: **CONNECTED**, Demo: `true`.
- Required market/spot/account modules: enabled via capability/status check.
- Market data: **PASS** for configured symbols during dry-run/scan.
- Account data: **PASS**.
- Wallet balances: BTC `1`, ETH `1`, OKB `100`, USDT `5000`.
- Managed positions: `0`.
- Managed open positions: `0`.
- Position slots in use: `0`.
- Reserved entry notional: `0`.
- OKX open orders: `[]`.
- Agent order lifecycle rows: `[]`.
- Pending executable plans: `[]` after the observed HOLD/REJECT analyses.
- Trades: `[]`.
- Recovery results: `[]`.
- Observed wallet exposure: approximately `90,436–90,438 USDT`.
- Observed total exposure ratio: approximately `94.78%`.
- Current eligibility: **BLOCKED — MAX_TOTAL_EXPOSURE_REACHED** (configured maximum 90%).
- BTC dry-run: HOLD, score 1/10, no order submission.
- Scan: BTC HOLD, ETH HOLD, SOL HOLD; no execution.

No secret was printed or placed in the report. No Demo/Live order, amendment,
cancellation or protective order was attempted.

## 10. Readiness

- Core validation status: **WORKING / SYSTEM CAPABILITY READY**.
- Demo execution status: **READY_FOR_CONTROLLED_DEMO_VALIDATION**; attached TP/SL
  capability is declared but a real protected order lifecycle is not order-verified.
- Current trading eligibility: **BLOCKED — MAX_TOTAL_EXPOSURE_REACHED**.
- AUTO_DEMO: **DISABLED**.
- Native API: **NOT_CONFIGURED**.
- Live trading: **LOCKED_NOT_IMPLEMENTED**.

This report does not claim FULLY PRODUCTION READY, READY FOR LIVE, profitable, or
validated by a real fill.

## 11. Known Limitations and Remaining Work

- Real attached TP/SL creation, trigger, fill and exit reconciliation remains
  **PARTIALLY_WORKING / NOT ORDER VERIFIED** because this audit prohibited orders.
- Protective exit reconciliation requires exchange fill IDs that match persisted
  protective IDs. An unlinked manual exit needs an explicit future control-service
  association; the transactional close model is present.
- STOPPED is process-local; restart returns the configured MANUAL_APPROVAL default.
  AUTO_DEMO remains disabled, so restart does not autonomously trade.
- The MCP request lock intentionally serializes calls; a future high-throughput
  service may need a dedicated single-writer worker/queue rather than concurrent
  direct backend access.
- SQLite remains appropriate for the local single-agent control service. It is not
  presented as a distributed multi-host database.
- Actual fee fields remain NULL/UNKNOWN when OKX does not return them; zero is never
  invented.
- Current account exposure must be reduced or the configured risk policy changed by
  the user before trading eligibility becomes true. This audit did neither.

Remaining P0: **none in deterministic core tests**. The first real protected Demo
order lifecycle is a controlled validation step requiring separate explicit user
authorization, not an audit-side fix performed here.
