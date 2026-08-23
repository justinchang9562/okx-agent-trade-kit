# Security Policy

## Supported scope

Version 0.3.0 is a localhost-only OKX Demo trading control plane. Live execution is locked and not
implemented. Binding the API outside `127.0.0.1`, adding CORS, or placing it behind an untrusted
proxy is outside the supported threat model.

The browser is a control and projection layer only. It must never receive exchange credentials or
submit symbol, side, size, entry, stop, take-profit, or order-type parameters. All trade decisions
remain owned by the Python pipeline and `RiskManager` can reject every entry.

## Credentials and sensitive artifacts

- Never commit OKX API keys, secret keys, passphrases, OAuth tokens, `.env` files, runtime databases,
  logs, caches, audit exports, or frontend build output.
- CI deliberately has no OKX credentials and excludes integration tests.
- Use the documented OS credential store and Demo-only MCP launcher. Do not paste credentials into
  issues, screenshots, browser storage, logs, or audit exports.
- If sensitive data is committed, revoke it immediately and remove it from repository history; a
  later deletion commit is not sufficient.

## Reporting a vulnerability

Report vulnerabilities privately to the repository owner. Include the affected version, minimal
reproduction, safety impact, and whether Demo order submission is involved. Do not submit a real or
Demo order as part of a proof of concept without separate explicit authorization.
