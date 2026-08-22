# Monitoring

Structured JSON events are written to `logs/trading_agent.log`. SQLite records
signals, risk rejections, plan state, every order transition, managed positions,
fills, actual-or-unknown fees and trades. Health probes check the selected
backend with real read-only calls. Secrets, tokens, passphrases, authorization
headers and `.env` contents must never be logged.
