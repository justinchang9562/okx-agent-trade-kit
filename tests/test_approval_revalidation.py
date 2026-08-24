from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from data.models import Balance
from execution.base_backend import BackendStatus
from execution.okx_adapter import OKXAdapter
from trading_agent.config import load_config
from trading_agent.orchestrator import TradingOrchestrator


class ReadOnlyBackend:
    name = "fake"
    def __init__(self, price=100.0): self.price = price
    def status(self): return BackendStatus("fake", "CONNECTED", True, True)
    def get_ticker(self, symbol): return {"data": {"data": [{"last": str(self.price)}]}}
    def close(self): pass


class MarketService:
    def __init__(self, market): self.market = market
    def get_snapshot(self, symbol): return self.market


class AccountService:
    def __init__(self, account): self.account = account
    def get_snapshot(self, symbol=None): return self.account


def agent(tmp_path, market, account):
    config = replace(load_config(), root=tmp_path)
    backend = ReadOnlyBackend(market.price)
    orchestrator = TradingOrchestrator(config, OKXAdapter("fake", backend))
    orchestrator.market_data = MarketService(market)
    orchestrator.account_data = AccountService(account)
    return orchestrator


def test_existing_demo_wallet_assets_do_not_trigger_max_open(tmp_path, market, account) -> None:
    wallet = replace(account, balances=(
        Balance("BTC", 1, 1), Balance("ETH", 1, 1), Balance("USDT", 5000, 5000),
    ))
    with agent(tmp_path, market, wallet) as orchestrator:
        plan = orchestrator.analyze("BTC-USDT")
        assert orchestrator.trade_store.managed_open_count() == 0
        assert plan.risk_status != "MAX_OPEN_POSITIONS_REACHED"


def test_expired_plan_is_rejected(tmp_path, market, account) -> None:
    with agent(tmp_path, market, account) as orchestrator:
        plan = orchestrator.analyze("BTC-USDT")
        orchestrator.trade_store.connection.execute(
            "UPDATE trade_plans SET expires_at_ms = 1 WHERE plan_id = ?", (plan.plan_id,)
        )
        orchestrator.trade_store.connection.commit()
        assert orchestrator.approve_plan(plan.plan_id)["reason"] == "PLAN_EXPIRED"


def test_price_moved_before_approval(tmp_path, market, account) -> None:
    with agent(tmp_path, market, account) as orchestrator:
        plan = orchestrator.analyze("BTC-USDT")
        moved = replace(market, price=market.price * 1.01, bid=market.price * 1.0099, ask=market.price * 1.0101)
        orchestrator.market_data = MarketService(moved)
        assert orchestrator.approve_plan(plan.plan_id)["reason"] == "PRICE_MOVED_TOO_FAR"


def test_stale_market_is_rechecked_on_approval(tmp_path, market, account) -> None:
    with agent(tmp_path, market, account) as orchestrator:
        plan = orchestrator.analyze("BTC-USDT")
        orchestrator.market_data = MarketService(replace(market, timestamp_ms=1))
        assert orchestrator.approve_plan(plan.plan_id)["reason"] == "STALE_DATA"


def test_spread_expansion_is_rechecked_on_approval(tmp_path, market, account) -> None:
    with agent(tmp_path, market, account) as orchestrator:
        plan = orchestrator.analyze("BTC-USDT")
        wider = replace(market, bid=market.price - .1, ask=market.price + .1)
        orchestrator.market_data = MarketService(wider)
        assert orchestrator.approve_plan(plan.plan_id)["reason"] == "SPREAD_TOO_WIDE"


def test_daily_kill_switch_after_plan_creation(tmp_path, market, account) -> None:
    with agent(tmp_path, market, account) as orchestrator:
        plan = orchestrator.analyze("BTC-USDT")
        now = int(datetime.now(timezone.utc).timestamp() * 1000)
        orchestrator.trade_store.connection.execute(
            """INSERT INTO trades
               (timestamp_ms, environment, backend, symbol, side, entry, size, stop,
                take_profit, pnl, strategy, signal_score)
               VALUES (?, 'demo', 'fake', 'BTC-USDT', 'LONG', 100, 1, 98, 103, -1600, 'rule_scalping_v1', 8)""",
            (now,),
        )
        orchestrator.trade_store.connection.commit()
        assert orchestrator.approve_plan(plan.plan_id)["reason"] == "DAILY_KILL_SWITCH_ACTIVE"
