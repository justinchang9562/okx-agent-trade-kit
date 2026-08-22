# Demo to live migration

Demo and future live execution must share indicators, strategy, risk, sizing,
decision, trade plan, storage, and metrics. Only backend/environment credentials
and final execution gates change.

Before live can be considered: sustain forward testing, reconcile fills/fees,
validate stop semantics and partial fills, add WebSocket recovery, monitoring and
alerts, independent kill controls, least-privilege live credentials without
withdrawal, deployment runbooks, and human review. Then enable all four gates:
live config, `LIVE_TRADING_ENABLED=true`, explicit CLI `--live`, and the exact
live confirmation. Current live remains locked and unimplemented.
