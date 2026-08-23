from __future__ import annotations

import threading
import time
from dataclasses import replace

import pytest

from tests.test_approval_revalidation import agent
from tests.test_web_service import ready_health
from trading_agent.config import load_config
from trading_agent.service import ServiceError, TradingService


class SlowBacktests:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self.closed = False

    def concurrent_with_runtime_supported(self) -> bool:
        return True

    def run(self, symbol: str, days: int, walk_forward: bool = False) -> dict:
        self.calls += 1
        self.started.set()
        assert self.release.wait(3)
        return {"symbol": symbol, "days": days, "walk_forward": walk_forward}

    def close(self) -> None:
        self.closed = True


def isolated_service(tmp_path, market, account, backtests: SlowBacktests) -> TradingService:
    orchestrator = agent(tmp_path, market, account)
    orchestrator.get_health = lambda: ready_health(orchestrator)
    return TradingService(
        replace(load_config(), root=tmp_path),
        orchestrator,
        start_scheduler=False,
        backtest_service=backtests,
    )


def test_slow_backtest_never_blocks_status_or_kill_switch(tmp_path, market, account) -> None:
    backtests = SlowBacktests()
    service = isolated_service(tmp_path, market, account, backtests)
    output: list[dict] = []
    worker = threading.Thread(target=lambda: output.append(service.run_backtest("BTC-USDT", 7)))
    try:
        worker.start()
        assert backtests.started.wait(1)
        started = time.monotonic()
        assert service.status()["live"]["execution"] == "LOCKED"
        service.activate_kill_switch()
        assert time.monotonic() - started < 0.5
        backtests.release.set()
        worker.join(2)
        assert output[0]["symbol"] == "BTC-USDT"
    finally:
        backtests.release.set()
        service.close()
    assert backtests.closed


def test_armed_execution_blocks_backtest_before_worker_submission(tmp_path, market, account) -> None:
    backtests = SlowBacktests()
    service = isolated_service(tmp_path, market, account, backtests)
    service.client_stream_connected("fresh")
    service.client_stream_acknowledged("fresh")
    try:
        service.set_mode("MANUAL_APPROVAL")
        service.start_agent()
        service.arm()
        with pytest.raises(ServiceError, match="BACKTEST_BLOCKED_WHILE_EXECUTION_ARMED"):
            service.run_backtest("BTC-USDT", 7)
        assert backtests.calls == 0
    finally:
        service.close()


def test_running_agent_requires_explicit_parallel_read_capability(tmp_path, market, account) -> None:
    backtests = SlowBacktests()
    backtests.concurrent_with_runtime_supported = lambda: False  # type: ignore[method-assign]
    service = isolated_service(tmp_path, market, account, backtests)
    try:
        service.set_mode("DRY_RUN")
        service.start_agent()
        with pytest.raises(ServiceError, match="BACKTEST_BACKEND_CONCURRENCY_UNAVAILABLE"):
            service.run_backtest("BTC-USDT", 7)
    finally:
        service.close()
