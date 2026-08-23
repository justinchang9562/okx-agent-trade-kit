from __future__ import annotations

from dataclasses import replace

import pytest

from execution.order_state import OrderState
from tests.test_approval_revalidation import agent
from trading_agent.web_api.security import SessionManager


def test_approval_challenge_is_single_use_and_bound_to_session_and_plan() -> None:
    manager = SessionManager()
    first = manager.create()
    second = manager.create()
    challenge = manager.create_approval_challenge(first.cookie, "plan-1", {"position_size": 1})
    token = str(challenge["approval_challenge"])
    with pytest.raises(ValueError, match="BINDING_MISMATCH"):
        manager.consume_approval_challenge(second.cookie, "plan-1", token)
    with pytest.raises(ValueError, match="INVALID_OR_USED"):
        manager.consume_approval_challenge(first.cookie, "plan-1", token)

    challenge = manager.create_approval_challenge(first.cookie, "plan-1", {"position_size": 1})
    token = str(challenge["approval_challenge"])
    assert manager.consume_approval_challenge(first.cookie, "plan-1", token)["position_size"] == 1
    with pytest.raises(ValueError, match="INVALID_OR_USED"):
        manager.consume_approval_challenge(first.cookie, "plan-1", token)


def test_expired_challenge_fails_closed(monkeypatch) -> None:
    import trading_agent.web_api.security as security

    clock = [100.0]
    monkeypatch.setattr(security.time, "monotonic", lambda: clock[0])
    manager = SessionManager()
    session = manager.create()
    token = str(manager.create_approval_challenge(
        session.cookie, "plan", {"position_size": 1}, ttl_seconds=1,
    )["approval_challenge"])
    clock[0] = 102.0
    with pytest.raises(ValueError, match="EXPIRED"):
        manager.consume_approval_challenge(session.cookie, "plan", token)


def test_changed_preview_requires_repreview_without_submitting(tmp_path, market, account) -> None:
    with agent(tmp_path, market, account) as orchestrator:
        plan = orchestrator.analyze("BTC-USDT")
        fresh = orchestrator.strategy.analyze(market)
        orchestrator.strategy.analyze = lambda snapshot: replace(
            fresh, suggested_take_profit=fresh.entry_price + 6, risk_reward=3,
        )
        preview = orchestrator.approve_plan(plan.plan_id)
        assert preview["status"] == "READY_FOR_EXACT_APPROVAL"
        stale_preview = preview | {
            "current_executable_price": float(preview["current_executable_price"]) * .99,
        }
        result = orchestrator.approve_plan(
            plan.plan_id,
            "CONFIRM DEMO ORDER",
            expected_preview=stale_preview,
        )
        assert result["reason"] == "APPROVAL_PREVIEW_CHANGED"
        assert orchestrator.trade_store.get_plan(plan.plan_id).status == OrderState.PLANNED.value
