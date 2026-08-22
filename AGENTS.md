This repository contains an OKX quantitative scalping trading system.

Codex must NEVER make an ad-hoc trading decision and directly execute it. For
trading requests, use the local pipeline: market data -> indicators -> scalping
strategy -> risk manager -> position sizing -> decision engine -> trade plan ->
order manager -> OKX adapter -> execution backend.

Always load `config/trading_rules.yaml`, `config/symbols.yaml`, and
`config/environments.yaml`. Never bypass `risk/risk_manager.py`. Strategy only
produces signals and must never submit orders. Use deterministic Python
indicators instead of manual calculations.

Never invent prices, balances, positions, orders, fills, fees, PnL, or market
data. If data cannot be retrieved, return `DATA_UNAVAILABLE` and do not trade.
The default is DEMO through the verified OKX Demo MCP backend. Unavailable CLI,
Native API, or live backends must not be represented as connected.

Never expose API keys, secrets, passphrases, or `.env` contents. When approval
is enabled, never execute before explicit user approval. Never martingale,
average down automatically, revenge trade, chase losses, remove stops, trade
stale/incomplete data, or increase risk after losses. Capital preservation and
deterministic risk management take priority over trade frequency.
