# Execution backends

The quant engine is backend-independent through `BaseBackend` and `OKXAdapter`.

## MCP — current primary

`MCPBackend` discovers and launches the existing `okx-mcp-demo-trade` stdio
wrapper, performs MCP initialization, and calls the upstream tools. It verifies
the server reports Demo mode plus enabled market, spot, and account modules.
Credentials remain outside the repository. Market/account/order queries and
guarded spot placement/cancel methods map to the real MCP tools.

The installed MCP metadata declares candle `after`/`before` pagination,
`clOrdId` order lookup, attached TP/SL fields on spot placement, algo placement,
and algo-order queries. Capability presence is checked from `tools/list`; this
does not claim that a protected Demo fill has been order-verified. A filled
entry without a matching protection order is persisted as
`POSITION_UNPROTECTED`. Transport failure after submission is never blindly
retried: it is stored as `SUBMISSION_UNKNOWN` and reconciled first.

## CLI — optional

`CLIBackend` detects `okx` and verifies the public `--demo` ticker path. It is
not the selected private execution backend and refuses place/cancel, so status is
`PARTIALLY_WORKING`, not connected execution.

## Native API — future

`NativeAPIBackend` is the REST/WebSocket extension boundary. Without explicit
configuration it returns `NOT_CONFIGURED` and every operation fails closed.

Only DemoExecutor is usable, after risk approval and explicit approval. Live
requires separate environment, environment variable, `--live` intent, and exact
confirmation; even then this release reports `LIVE_TRADING_NOT_IMPLEMENTED`.
