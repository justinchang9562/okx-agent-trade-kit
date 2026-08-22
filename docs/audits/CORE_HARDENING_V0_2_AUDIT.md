# OKX Trading Agent Core Hardening v0.2 Audit

Audit date: 2026-08-22 (Asia/Taipei)

Project version: 0.2.0

Scope: core trading architecture, state integrity, execution safety, idempotency,
recovery, historical data, backtesting and auditability.

Explicit exclusion: no Demo/Live order, amend, cancel, transfer, Web UI, macOS
application, ML model, scheduler or live-trading implementation was performed.

## 1. Executive Summary

The v0.1 source was re-read directly; this audit did not rely on the prior report.
The highest-impact confirmed defect was in `trading_agent/orchestrator.py`: every
configured base-currency wallet balance greater than zero was counted as an open
position. The Demo wallet's BTC and ETH inventory therefore produced an open
count of two and incorrectly activated `MAX_OPEN_POSITIONS_REACHED` even though
the Agent had created no trade.

v0.2 replaces this meaning with an explicit, SQLite-backed `ManagedPosition`.
`max_open_positions` now counts only Agent-originated active lifecycles. Wallet
inventory remains risk-relevant through the independent, price-based
`max_total_exposure_pct` rule. Read-only Demo evidence showed BTC=1, ETH=1,
OKB=100 and USDT=5000, but zero managed positions and zero open orders. After
all assets were priced from real tickers, wallet exposure was approximately
90,786 USDT, or 94.776% of reported equity. A new BTC dry run was therefore
correctly rejected by `MAX_TOTAL_EXPOSURE_REACHED`, not by the managed-position
limit.

The release adds a single approval boundary by persisted `plan_id`, plan TTL,
fresh approval-time validation, price/slippage guards, persistent order states,
atomic duplicate-approval protection, client-order-ID reconciliation,
`SUBMISSION_UNKNOWN`, restart recovery, partial-fill handling and the
`POSITION_UNPROTECTED` fail-safe state. The real MCP tool metadata was inspected
read-only and declares candle pagination, `clOrdId` lookup, attached TP/SL fields,
algo placement and algo-order queries. These capabilities were not order-tested.

Forty-three tests passed. Real Demo read-only market, account, balance, orders,
fills, health, scan, analysis, pending-plan and approval-preview paths passed.
No exact approval was sent to the real backend. The release is suitable only for
the next controlled milestone: **READY FOR FIRST CONTROLLED DEMO ORDER**. It is
not production-ready; Live remains **LOCKED / NOT IMPLEMENTED**.

## 2. Files Changed

Major modified files:

- `config/trading_rules.yaml`: total exposure, TTL, entry deviation, retry,
  protection, historical and runtime configuration.
- `trading_agent/orchestrator.py`: managed-position semantics, exposure pricing,
  persisted plans, approval-time revalidation, preview, recovery and clean core
  API methods.
- `trading_agent/cli.py`: `pending`, `approve`, `reject`, `recover`, `trades`,
  `calibration`, paginated backtest and walk-forward commands.
- `trading_agent/state.py`: STOPPED, DRY_RUN, MANUAL_APPROVAL and disabled
  AUTO_DEMO runtime states.
- `strategies/signal.py`, `strategies/scalping_strategy.py`,
  `decision/trade_plan.py`, `decision/decision_engine.py`: `signal_strength`,
  plan creation/expiration/status and compatibility aliases.
- `risk/exposure.py`, `risk/risk_manager.py`: separate wallet/managed exposure,
  production risk evaluation timestamp injection and total-exposure veto.
- `execution/base_backend.py`, `execution/mcp_backend.py`,
  `execution/demo_executor.py`, `execution/order_manager.py`,
  `execution/order_state.py`: capability inspection, pagination, idempotency,
  transition persistence, reconciliation and protection states.
- `storage/database.py`, `storage/trade_store.py`, `storage/signal_store.py`:
  additive v0.1 migration, plans, lifecycles, managed positions, actual/unknown
  fill data, calibration and atomic approval.
- `data/models.py`, `data/normalizer.py`: partial fill, average price and nullable
  fee semantics.
- `backtest/backtester.py`: next-bar execution, production RiskManager reuse,
  historical state and explicit costs.
- `README.md`, `.gitignore`, `.env.example` and documents under `docs/`.

New files:

- `trading_agent/control_api.py`
- `data/historical.py`
- `backtest/cost_models.py`
- `backtest/walk_forward.py`
- `tests/test_core_hardening.py`
- `tests/test_approval_revalidation.py`
- `tests/test_historical_backtest_foundation.py`
- `docs/audits/CORE_HARDENING_V0_2_AUDIT.md`

## 3. P0 Findings

1. **Wallet/position conflation — confirmed.** Non-zero BTC/ETH balances were
   used directly as open-position count.
2. **No standard approval command — confirmed.** OrderManager could only be
   reached by constructing Python objects.
3. **No plan TTL/status persistence — confirmed.** A stale scalping decision had
   no formal expiration gate.
4. **No approval-time price deviation check — confirmed.** Old entry prices could
   survive until submission.
5. **Slippage only partially modeled — confirmed.** No post-fill persisted
   calculation existed.
6. **Order enum not wired to lifecycle persistence — confirmed.** Only a final
   submission response was stored.
7. **Response-loss duplicate risk — confirmed.** The old implementation wrote
   `order_submissions` only after the backend returned successfully.
8. **No restart recovery — confirmed.** Submitted/unknown orders could not be
   restored after a crash.
9. **TP/SL not capability-verified — confirmed.** Attached fields were sent in
   code but there was no protection verification or unprotected state.
10. **Fee/PnL defaults were misleading — confirmed.** Missing fees defaulted to
    zero and trade fields did not distinguish estimates from actuals.

## 4. P0 Fixes

| Control | Status | Evidence |
|---|---|---|
| Managed-position counting | WORKING | SQLite model, orchestration and tests; real wallet with zero managed positions |
| Independent total exposure | WORKING | Real BTC/ETH/OKB tickers; `MAX_TOTAL_EXPOSURE_REACHED` |
| `approve PLAN_ID` boundary | WORKING | CLI/core API; read-only no-confirmation preview |
| Plan TTL/status | WORKING | persisted fields and expiry test |
| Approval stale/spread/daily recheck | WORKING | deterministic tests |
| Entry deviation/pre-submit guard | WORKING | deterministic test and real preview |
| Post-fill slippage | PARTIALLY WORKING | calculation/persistence tests; no real fill verification |
| Persistent lifecycle | WORKING | state table, transition validation and tests |
| Idempotency/reconciliation | WORKING | atomic plan approval, unique plan/client ID and response-loss test |
| Restart recovery | WORKING | active-order restart test; no real active order existed |
| TP/SL protection verification | PARTIALLY WORKING | MCP metadata + mocked failure; no controlled Demo fill yet |
| Actual fill/fee/PnL | PARTIALLY WORKING | nullable schema/backfill; no real round trip or fee-currency conversion |

## 5. Managed Position Semantics

`ManagedPosition` is created only after an Agent-owned order has a partial or
full fill. Active managed states are `PARTIALLY_FILLED`, `FILLED` and
`POSITION_UNPROTECTED`; `CLOSED` positions no longer count. `DailyRiskState` now
obtains `open_position_count` from this table by default.

Wallet balances are a separate account observation. `ExposureSnapshot` records
wallet exposure, managed exposure, projected total exposure, percentage,
priced currencies and unpriced currencies. Managed spot holdings are not added
twice after they already exist in wallet inventory. Proposed order notional is
added before the total-exposure decision.

The three required cases are covered:

- BTC/ETH wallet inventory + zero managed trades does not activate
  `MAX_OPEN_POSITIONS_REACHED`.
- Two active Agent-managed positions activate the configured limit.
- BTC inventory and an Agent-managed BTC position remain separately observable.

## 6. Approval / Execute Flow

The only standard command is `python -m trading_agent approve PLAN_ID`. The
future `LocalControlAPI.approve_plan()` has the same contract. Symbol, side and
quantity cannot be supplied by a UI as a replacement order.

The path is: load plan; verify pending status; verify TTL; fetch fresh market and
account; price wallet assets; check planned entry against current ask; recreate
the deterministic signal; rerun stale, spread, score, signal-strength, daily
loss, consecutive loss, managed positions, cooldown, duplicate and total
exposure rules; recalculate quantity; check pre-submit slippage; construct the
final preview; require an exact approval phrase; atomically transition/insert the
order; then call OrderManager -> DemoExecutor -> backend.

Without the exact phrase the real preview returned
`BLOCKED_EXPLICIT_APPROVAL_REQUIRED`. No execution object was called.

## 7. TradePlan TTL

Plans persist `created_at_ms`, `expires_at_ms` and `status`. Default TTL is 60
seconds. Listing pending plans expires stale rows, and approval independently
rechecks time. An expired plan becomes REJECTED with `PLAN_EXPIRED`; it cannot
be revived or submitted.

## 8. Price Deviation Guard

At approval the Agent compares the current executable ask with the original
entry using `abs(current - planned) / planned * 100`. The configurable default
limit is 0.15 percentage points. Exceeding it produces
`PRICE_MOVED_TOO_FAR` and requires a new analysis. The real safe preview observed
a 0.015139% deviation and remained blocked pending exact approval.

## 9. Idempotency

The deterministic plan ID is also the client order ID (within OKX length limits).
Plan approval and lifecycle creation occur in one `BEGIN IMMEDIATE` transaction;
the plan row must still be PLANNED and the lifecycle has unique `plan_id` and
`client_order_id` constraints.

Lifecycle state is committed as SUBMITTED before the external call. Any MCP or
transport exception after the call begins becomes `SUBMISSION_UNKNOWN`.
OrderManager queries by client order ID, then by known OKX order ID if available.
It never calls `place_order` again in this path. Tests cover timeout before an
observable acceptance, response loss after acceptance, duplicate approval,
duplicate submission and MCP process failure.

## 10. Order State Persistence

SQLite records plan ID, client/OKX order IDs, symbol, side, requested/filled
size, average fill, expected price, absolute/percentage slippage, nullable fee,
state, protection state, timestamps, backend, environment, response and error.
Allowed transitions are validated. Supported paths include PLANNED -> APPROVED
-> SUBMITTED -> OPEN/PARTIALLY_FILLED/FILLED, SUBMISSION_UNKNOWN reconciliation,
REJECTED, CANCELLED, POSITION_UNPROTECTED and CLOSED. A rejected cancellation
does not write CANCELLED.

## 11. Restart Recovery

`recover` loads every active lifecycle, queries the backend by client order ID
or OKX order ID and applies the normalized remote state. A test starts from a
persisted SUBMITTED row, constructs a new OrderManager, observes the remote
order and restores OPEN. Production recovery is polling-based; WebSocket event
replay is not implemented.

## 12. TP/SL Protection Model

Read-only MCP `tools/list` evidence:

- `spot_place_order` declares `tpTriggerPx`, `tpOrdPx`, `slTriggerPx` and
  `slOrdPx`.
- `spot_place_algo_order` declares conditional TP/SL parameters.
- `spot_get_algo_orders` is present.
- `spot_get_order` supports `clOrdId`.

DemoExecutor fails before entry if attached protection is not a declared
capability. After a fill, reconciliation queries algo orders. A filled entry
without matching protection becomes `POSITION_UNPROTECTED` with
`STOP_LOSS_NOT_VERIFIED`. The system records the highest-priority state but does
not autonomously place a protective exit in this release, because that policy
would itself be an order action requiring a separately approved design.

Capability metadata is **PARTIALLY WORKING**, not proof of a protected Demo fill.
The first controlled Demo order must verify actual attachment and stop failure.

## 13. Fill / Fee / PnL Model

Partial fills record `filled_size` and do not become FILLED. Average fill and
post-fill slippage are based on actual remote values. Fill fees remain NULL when
OKX omits them; zero is never substituted. Entry trade rows retain plan/order
IDs, entry time/price, quantity, strategy and score. Close support records exit,
gross PnL, holding time, fees and net PnL only when required actual values are
available.

Remaining limitation: OKX fee sign, fee currency and conversion to the PnL quote
currency require validation from a real fill/round trip. Therefore end-to-end
actual net PnL is **PARTIALLY WORKING** and is not claimed as verified.

## 14. Backtest Data Improvements

`HistoricalDataService` supports configured 7/30/90-day horizons, 100-bar pages,
`after` pagination, ignored checkpoint cache and finite 429 backoff. Integrity
checks cover symbol/timeframe context, ordering, duplicates, missing intervals,
OHLC bounds, volume and confirmed state. Gaps are repaired only by targeted real
OKX requests; no candle is fabricated. Unresolved gaps fail with `DATA_GAP`.

The first full request encountered HTTP 429 and then a single pagination gap.
Both were reported rather than hidden. After checkpoint resume and targeted real
gap retrieval, 7-day integrity passed:

- 1m: 10,080 bars, zero duplicates/gaps/unconfirmed.
- 3m: 3,360 bars, zero duplicates/gaps/unconfirmed.
- 5m: 2,016 bars, zero duplicates/gaps/unconfirmed.

Thirty- and ninety-day code paths are implemented but were not fully downloaded
in this audit; they remain **PARTIALLY WORKING / NOT LOAD-VERIFIED** under the
observed OKX rate limit.

## 15. Cost Model

Fees, spread and slippage are injected through `FixedFeeModel`,
`FixedSpreadModel`/`ObservedSpreadModel` and
`FixedSlippageModel`/`EmpiricalSlippageModel`. Current runs use fixed configured
fallbacks and identify them in output. Execution occurs at the next bar open,
not the signal candle close. When stop and target are both touched in one candle,
the simulator chooses the adverse stop outcome.

The production RiskManager is reused with historical timestamps and a
HistoricalAccount/Risk state equivalent for equity, daily PnL, consecutive
losses, cooldown and sequential open-position count. No strategy parameter was
changed. The 7-day run produced 42 trades; this is execution evidence, not a
profitability claim.

Six 1,440-bar rolling out-of-sample windows ran without optimization. Window net
results included negative and positive values, demonstrating that the report did
not select only favorable periods.

## 16. Test Matrix

Command: `uv run pytest -q`

Result: **43 passed, 0 failed**.

Covered critical cases include indicator/strategy/risk/sizing baselines plus:

1. wallet assets are not managed positions;
2. two managed positions reach the limit;
3. wallet and managed BTC remain distinct;
4. partial fill stays PARTIALLY_FILLED;
5. timeout before observable acceptance does not retry;
6. response loss after acceptance reconciles by client ID;
7. duplicate approval is blocked;
8. duplicate submission is blocked;
9. expired plan is rejected;
10. approval-time price movement is rejected;
11. stale approval-time market is rejected;
12. expanded approval-time spread is rejected;
13. daily kill switch activated after creation is respected;
14. MCP/process failure fails closed;
15. restart restores an active order;
16. missing stop protection becomes POSITION_UNPROTECTED;
17. rejected cancel does not claim CANCELLED;
18. missing fee remains NULL/UNKNOWN;
19. historical gaps/duplicates/unconfirmed bars are detected;
20. cost and walk-forward abstractions are deterministic.

Static import check: `python -m compileall` passed.

## 17. Real Demo Read-Only Evidence

All calls were market/account/order/fill/tool-metadata reads. No place, amend,
cancel or transfer call was made.

- MCP: CONNECTED; Demo: true.
- Market data: PASS.
- Account data: PASS.
- Wallet: BTC 1.0; ETH 1.0; OKB 100.0; USDT 5000.0.
- Agent-managed positions: 0.
- OKX open orders: 0.
- Returned fills: 0.
- Local order lifecycles: 0.
- Pending plans after TTL cleanup: 0.
- Known wallet exposure: about 90,786 USDT / 94.776%; no unpriced currencies.
- BTC dry run: LONG rule signal, rejected by `MAX_TOTAL_EXPOSURE_REACHED`.
- Health: `READY_FOR_FIRST_CONTROLLED_DEMO_ORDER` with explicit approval;
  attached protection capability declared but not order-verified.

## 18. Known Limitations

- Actual attached TP/SL and post-fill reconciliation need one controlled Demo
  order and fill.
- Recovery is polling-based; no WebSocket replay or distributed process lease.
- `POSITION_UNPROTECTED` is persisted but automated protective exit is not
  implemented or authorized.
- Actual fee sign/currency conversion and complete round-trip net PnL are not
  Demo-verified.
- Thirty-/ninety-day pagination is implemented but not load-verified.
- Observed/empirical cost models need enough real spread/fill samples.
- Signal calibration returns score buckets only when completed real trades exist;
  it does not call normalized score an empirical probability.
- AUTO_DEMO remains disabled; no scheduler exists.
- CLI private execution and Native API remain NOT CONFIGURED.
- Live remains LOCKED / NOT IMPLEMENTED.

## 19. Remaining Work

Remaining P0 before any broader Demo automation:

1. Execute one separately authorized, minimal controlled Demo trade.
2. Verify accepted client order ID, partial/full lifecycle and restart recovery
   against real remote states.
3. Verify attached stop/take-profit presence and intentionally test a mocked or
   safely controlled protection-failure response.
4. Verify actual fill fee sign/currency, average price and post-fill slippage.
5. Define an explicitly approved protective-exit policy for
   POSITION_UNPROTECTED.

Remaining P1:

1. Run and integrity-verify 30-/90-day histories within documented rate limits.
2. Accumulate actual costs for empirical models.
3. Build alert delivery and operational reconciliation runbooks.
4. Add WebSocket/event replay and a single-writer runtime lease.
5. Accumulate sufficient outcome samples for empirical score calibration.

## 20. Final Readiness

- Core market/account/strategy/risk/plan path: **WORKING**.
- Managed position semantics and wallet exposure separation: **WORKING**.
- Approval TTL/fresh guards/price deviation: **WORKING**.
- Persistent lifecycle/idempotency/restart tests: **WORKING**.
- Real post-fill/protection/fee round trip: **PARTIALLY WORKING**.
- 7-day paginated backtest and walk-forward: **WORKING**.
- 30-/90-day load verification: **PARTIALLY WORKING**.
- CLI private execution: **NOT CONFIGURED**.
- Native API: **NOT CONFIGURED**.
- AUTO_DEMO: **LOCKED / disabled**.
- Live: **LOCKED / NOT IMPLEMENTED**.

Final classification: **READY FOR FIRST CONTROLLED DEMO ORDER**.

Explicitly not classified as fully production-ready or ready for Live.
