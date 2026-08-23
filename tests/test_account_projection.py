from __future__ import annotations

from dataclasses import replace

from data.models import Balance, Fill, Order
from execution.order_state import OrderState
from tests.test_approval_revalidation import AccountService, agent
from tests.test_core_hardening import plan_for


def test_account_projection_separates_agent_and_external_ownership(
    tmp_path, market, account, long_signal,
) -> None:
    with agent(tmp_path, market, account) as orchestrator:
        plan = plan_for(long_signal)
        orchestrator.trade_store.save_plan(plan)
        assert orchestrator.trade_store.approve_and_create_order(
            plan, plan.plan_id, plan.entry,
        )
        orchestrator.trade_store.transition_order(
            plan.plan_id, OrderState.SUBMITTED.value, okx_order_id="agent-order-1",
        )
        snapshot = replace(
            account,
            balances=(
                Balance("USDT", 5_000, 5_000),
                Balance("BTC", 1.0, 1.0),
            ),
            open_orders=(
                Order(
                    "agent-order-1", plan.plan_id, plan.symbol, "buy", "limit",
                    "live", plan.position_size, plan.entry, plan.created_at_ms,
                ),
                Order(
                    "external-order-1", "manual-client", plan.symbol, "sell", "limit",
                    "live", 0.1, plan.entry + 10, plan.created_at_ms,
                ),
            ),
            fills=(
                Fill(
                    "agent-fill", "agent-order-1", plan.symbol, "buy", 0.01,
                    plan.entry, 0.001, plan.created_at_ms, "USDT",
                ),
                Fill(
                    "external-fill", "external-order-1", plan.symbol, "sell", 0.01,
                    plan.entry, 0.001, plan.created_at_ms, "USDT",
                ),
            ),
        )
        orchestrator.account_data = AccountService(snapshot)

        projection = orchestrator.synchronize_account()

    orders = projection["orders"]["okx_open_orders"]
    assert [item["origin"] for item in orders] == ["AGENT", "EXTERNAL"]
    assert [item["origin"] for item in projection["fills"]] == ["AGENT", "EXTERNAL"]
    assert projection["positions"]["external_wallet_inventory"] == [{
        "currency": "BTC", "quantity": 1.0, "origin": "EXTERNAL", "managed": False,
    }]
