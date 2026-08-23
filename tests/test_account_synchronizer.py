from __future__ import annotations

import threading
import time

from trading_agent.account_synchronizer import AccountSynchronizer


def test_account_synchronizer_is_single_flight() -> None:
    synchronizer = AccountSynchronizer(minimum_interval_seconds=10)
    calls = 0
    barrier = threading.Barrier(3)
    results: list[dict] = []

    def load() -> dict:
        nonlocal calls
        calls += 1
        time.sleep(0.02)
        return {"account": {"equity_usdt": 100}}

    def worker() -> None:
        barrier.wait()
        results.append(synchronizer.synchronize(load))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    assert calls == 1
    assert results == [
        {"account": {"equity_usdt": 100}},
        {"account": {"equity_usdt": 100}},
    ]
