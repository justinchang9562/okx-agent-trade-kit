from __future__ import annotations

import threading
import time

import pytest

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


def test_loader_exception_releases_single_flight_for_next_caller() -> None:
    synchronizer = AccountSynchronizer(minimum_interval_seconds=0)
    with pytest.raises(RuntimeError, match="loader failed"):
        synchronizer.synchronize(lambda: (_ for _ in ()).throw(RuntimeError("loader failed")))
    assert synchronizer.synchronize(lambda: {"ok": True}) == {"ok": True}


def test_minimum_interval_returns_cached_projection() -> None:
    synchronizer = AccountSynchronizer(minimum_interval_seconds=10)
    calls = 0

    def loader() -> dict:
        nonlocal calls
        calls += 1
        return {"call": calls}

    assert synchronizer.synchronize(loader) == {"call": 1}
    assert synchronizer.synchronize(loader) == {"call": 1}
    assert calls == 1


def test_closed_synchronizer_fails_waiters_closed() -> None:
    synchronizer = AccountSynchronizer()
    synchronizer.close()
    with pytest.raises(RuntimeError, match="ACCOUNT_SYNCHRONIZER_CLOSED"):
        synchronizer.synchronize(lambda: {})


def test_shutdown_releases_waiter_and_rejects_inflight_result() -> None:
    synchronizer = AccountSynchronizer(minimum_interval_seconds=0)
    loader_started = threading.Event()
    loader_release = threading.Event()
    errors: list[str] = []

    def loader() -> dict:
        loader_started.set()
        loader_release.wait(timeout=2)
        return {"ok": True}

    def worker(load) -> None:
        try:
            synchronizer.synchronize(load)
        except RuntimeError as exc:
            errors.append(str(exc))

    inflight = threading.Thread(target=worker, args=(loader,))
    waiter = threading.Thread(target=worker, args=(lambda: {"unexpected": True},))
    inflight.start()
    assert loader_started.wait(timeout=2)
    waiter.start()
    synchronizer.close()
    waiter.join(timeout=2)
    loader_release.set()
    inflight.join(timeout=2)

    assert not inflight.is_alive()
    assert not waiter.is_alive()
    assert errors == ["ACCOUNT_SYNCHRONIZER_CLOSED", "ACCOUNT_SYNCHRONIZER_CLOSED"]
