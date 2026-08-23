# Release Checklist

## Safety boundary

- [ ] Environment defaults to Demo and Live remains `LOCKED_NOT_IMPLEMENTED`.
- [ ] Startup resets execution to DISARMED, agent to STOPPED, and AUTO off.
- [ ] An ACTIVE Kill Switch remains active across restart until exact reset confirmation.
- [ ] No browser or API schema accepts exchange order parameters.
- [ ] TP/SL verification, exposure limits, slippage checks, Risk Manager and approval remain enabled.

## Verification

- [ ] `uv sync --extra dev --locked`
- [ ] `uv run ruff check .`
- [ ] `uv run pytest -m "not integration"`
- [ ] `uv run python -m compileall trading_agent data indicators strategies risk decision execution backtest storage monitoring`
- [ ] `cd frontend && npm ci && npm test -- --run && npm run build`
- [ ] `uv run python scripts/check_repo_safety.py`
- [ ] Review `git diff --check`, `git status --short`, and `git diff --stat`.

## Evidence and publication

- [ ] Update `CHANGELOG.md`, README, API docs, security policy, and remediation audit evidence.
- [ ] Record whether a real Demo lifecycle was actually executed; never imply it if only mocks passed.
- [ ] Review dependency audit output without silently weakening tests or safety rules.
- [ ] Require branch protection and passing CI before merge.
- [ ] Tag the reviewed commit with the unified project release version.
- [ ] Verify no credentials exist in the complete Git history before making the repository public.
