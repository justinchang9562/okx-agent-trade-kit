# Final Architecture — Operator-Controlled Automatic Trading Agent

## Product boundary

    Codex / AI
      explains market, risk, trades and performance
      never participates in tick-to-order execution

    User
      |
      v
    Local Dashboard: START / PAUSE / STOP / FLATTEN ALL & STOP
      |
      v
    AutoTradingSessionController
      |-----------------------------------|
      v                                   v
    OKX Public WebSocket              AccountSynchronizer
      |                               account/orders/fills (3s, single-flight)
      v                                   |
    RealtimeMarketState                   |
      |                                   |
      v                                   |
    confirmed 1m -> Strategy              |
      |                                   |
      v                                   |
    RiskManager <-------------------------|
      |
    Position Sizing
      |
    Trade Decision / immutable TradePlan
      |
    final realtime revalidation
      |
    OrderManager
      |
    OKX Agent Trade Kit MCP
      |
    OKX Demo

The product is one Python process, one local React Dashboard, one SQLite database, one OKX
Agent Trade Kit connection and one public realtime market stream. It deliberately does not add
microservices, queues, Redis, Kafka, private REST reimplementation, derivatives or AI execution.

## Authoritative responsibilities

AutoTradingSessionController is the sole owner of user-level START, PAUSE, STOP and FLATTEN
semantics. React only calls session endpoints. FastAPI performs local session/CSRF/loopback checks
and forwards the request. TradingService serializes state changes and Core calls. Compatibility
Mode/ARM/AUTO/Approval endpoints remain deprecated and are not part of the main UI.

TradingOrchestrator owns normalized market/account inputs, deterministic indicators, strategy,
RiskManager, sizing and final plan reconstruction. Strategy emits a Signal only and cannot import
or call execution. RiskManager retains final veto at plan creation and again immediately before
submission.

OrderManager remains the only order lifecycle owner. Entry execution and managed exits go through
DemoExecutor and the OKX Agent Trade Kit MCP backend. An order intent is persisted before a write.
SUBMISSION_UNKNOWN is reconciled by client order ID and never blindly retried.

## Realtime market path

RealtimeMarketRuntime connects to OKX official public/business Demo WebSocket endpoints and
subscribes only to tickers, books5 and candle1m/candle3m/candle5m for BTC-USDT, ETH-USDT and
SOL-USDT. It maintains bounded local buffers, validates timestamps/OHLC/book values, rejects
duplicates and out-of-order changes, detects missing intervals, and requires full resync after
reconnect.

Historical candle and instrument bootstrap remains read-only through Agent Trade Kit/MCP. Once
ready, TradingOrchestrator reads MarketSnapshot from RealtimeMarketState. Full strategy evaluation
runs once per confirmed 1m candle. Ticker/book changes update price, spread and final execution
revalidation only.

Disconnect, stale ticker/book/candles, incomplete buffers or failed resync disarms execution and
moves a running session to DEGRADED. Existing SL/TP and reconciliation remain active.

## Session state

The user-visible states are STOPPED, RUNNING, PAUSED, FLATTENING and DEGRADED. Internal
ExecutionState, TradingMode and AgentRuntimeState remain compatibility/safety details.

- START runs all preflight reads and reconciliation before one atomic transition to RUNNING,
  AUTO enabled and ARMED. Double START is idempotent.
- PAUSE first disarms and blocks entries, then cancels only Agent entry orders. Runtime,
  market, account, order, fill, position and protection monitoring continue.
- STOP first disarms and blocks entries, then cancels Agent entry orders and ends the runtime
  session. Managed positions and their SL/TP remain.
- FLATTEN first disarms and persists FLATTENING, cancels Agent entry orders, then creates one
  persistent close intent per Agent-managed position. It reaches STOPPED/FLAT only after managed
  quantity is reconciled to zero.
- Process startup always persists STOPPED, DISARMED and AUTO off while preserving Kill Switch and
  reconciling existing managed positions.

## Ownership and flatten safety

ManagedPosition rows are created only from Agent order lifecycle fills. Wallet balances do not
become managed merely because an asset exists. Account projections tag open orders and fills as
AGENT only when their order/client ID matches persisted lifecycle data; otherwise they are
EXTERNAL. External inventory is wallet balance minus persisted managed quantity.

External assets remain visible and count toward total exposure. They are never auto-adopted,
cancelled, modified or flattened. There is no account-wide flatten mode.

flatten_intents stores plan ID, a deterministic hashed client order ID, requested and filled
quantity, exchange order ID and submission state. Repeated calls reconcile the existing intent.
Protection orders are cancelled only after the associated managed position is confirmed closed.

## Protection verification

A filled Agent entry is PROTECTED only when reconciliation finds:

- both SL and TP linked to the correct entry plan/order;
- active/effective order state;
- trigger prices equal to the final plan using instrument tick-size tolerance;
- each protection quantity covering actual filled position quantity.

Missing, inactive, unknown, price-mismatched or quantity-mismatched protection becomes
POSITION_UNPROTECTED. This immediately blocks further entries and causes a running session to
degrade while reconciliation continues.

## Account synchronization

One serialized synchronization call fetches account, open orders and recent fills, then fans out
account.updated, orders.updated, positions.updated and fills.updated only when projections change.
The default interval is three seconds and overlapping polling is impossible because all Core/MCP
access is serialized.

## Order lifecycle fast lane

Normal account projection remains a three-second single-flight loop. A newly submitted order or an
accepted cancel request additionally receives four bounded, serialized, single-order lookups at
`0s`, `0.3s`, `1s` and `2s`. This is not a permanent poller.

Cancel acknowledgement is nonterminal: `OPEN/PARTIALLY_FILLED → CANCEL_REQUESTED`. Targeted
reconciliation alone may promote it to `CANCELLED`, `FILLED`, or an updated partial managed
position. `SUBMISSION_UNKNOWN` continues to use client-order lookup first and never resubmits.

Flatten is an ordered attempt ledger. Each terminal, fully reconciled failed/cancelled attempt may
produce one new deterministic client ID for the remaining quantity computed from deduplicated,
persisted exit fills. Active or uncertain attempts forbid another close. Protection cleanup starts
only after the managed position and trade are durably closed; failures persist as
`PROTECTION_CLEANUP_INCOMPLETE` for background retry.

## Realtime transport hardening

A large books5 sequence regression moves the symbol to `RESYNCING` and fails the endpoint so both
feeds reconnect and bootstrap before entries resume. Heartbeat processing accepts ticker/book/candle
messages while awaiting pong. Confirmed-candle work uses a bounded deduplicating queue; overflow
degrades the session and events older than five seconds are discarded as `STALE_STRATEGY_EVENT`.

## Durable safety core

SQLite persists TradePlan -> OrderLifecycle -> ManagedPosition -> Trade plus reconciliation cursors,
fill evidence, control audit and ordered flatten attempts. WAL, FULL synchronous mode, bounded busy timeout,
CAS lifecycle transitions and per-store reentrant locks remain.

Backtests use an independent read-only backend and worker and cannot call OrderManager. Live remains
LOCKED / NOT IMPLEMENTED. Browser/API projections never receive credentials or backend objects.
