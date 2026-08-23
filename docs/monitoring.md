# Monitoring

Structured JSON events are written to `logs/trading_agent.log`. SQLite records
signals, risk rejections, plan state, every order transition, managed positions,
fills, actual-or-unknown fees and trades. Health probes check the selected
backend with real read-only calls. Secrets, tokens, passphrases, authorization
headers and `.env` contents must never be logged.

The Dashboard status projection also reports client-ACK age, last scan, last
health, nearest pending-plan expiry, wallet exposure percentage, managed
exposure, daily PnL and consecutive losses. Reconciliation writes structured
audit rows and a durable cursor; incomplete history remains visible as a
fail-closed error rather than an inferred close.
