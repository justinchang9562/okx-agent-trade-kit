from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any


class AccountSynchronizer:
    """Small in-process single-flight gate for account/order/fill reconciliation."""

    def __init__(self, minimum_interval_seconds: float = 1.0) -> None:
        self.minimum_interval_seconds = minimum_interval_seconds
        self._condition = threading.Condition(threading.RLock())
        self._running = False
        self._last_completed = 0.0
        self._last_projection: dict[str, Any] | None = None

    def synchronize(self, loader: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        with self._condition:
            while self._running:
                self._condition.wait()
            age = time.monotonic() - self._last_completed
            if self._last_projection is not None and age < self.minimum_interval_seconds:
                return self._last_projection
            self._running = True
        try:
            projection = loader()
            if not isinstance(projection, dict):
                raise TypeError("ACCOUNT_SYNCHRONIZER_INVALID_PROJECTION")
        finally:
            with self._condition:
                self._running = False
                self._condition.notify_all()
        with self._condition:
            self._last_projection = projection
            self._last_completed = time.monotonic()
            return projection
