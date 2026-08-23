from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from backtest.backtester import Backtester
from execution.base_backend import BaseBackend
from execution.mcp_backend import public_read_only_mcp_backend
from execution.okx_adapter import OKXAdapter
from execution.read_only_backend import ReadOnlyBackend
from trading_agent.config import AppConfig


class BacktestService:
    """Owns a dedicated worker and a separate, write-disabled market-data backend."""

    def __init__(
        self,
        config: AppConfig,
        backend_factory: Callable[[], BaseBackend] | None = None,
    ) -> None:
        self.config = config
        if backend_factory is not None:
            self._backend_factory = backend_factory
        elif config.backend == "mcp":
            self._backend_factory = public_read_only_mcp_backend
        else:
            self._backend_factory = lambda: OKXAdapter(config.backend).backend
        self._backend: ReadOnlyBackend | None = None
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="backtest")
        self._lock = threading.RLock()
        self._closed = False

    def _read_only_backend(self) -> ReadOnlyBackend:
        with self._lock:
            if self._closed:
                raise RuntimeError("BACKTEST_SERVICE_CLOSED")
            if self._backend is None:
                self._backend = ReadOnlyBackend(self._backend_factory())
            return self._backend

    def concurrent_with_runtime_supported(self) -> bool:
        try:
            return bool(self._read_only_backend().capabilities().get("concurrent_read_only", False))
        except Exception:
            return False

    def run(self, symbol: str, days: int, walk_forward: bool = False) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("BACKTEST_SERVICE_CLOSED")

        def execute() -> dict[str, Any]:
            backtester = Backtester(
                self._read_only_backend(),
                self.config.rules,
                self.config.root / "data_cache",
            )
            return backtester.walk_forward(symbol, days) if walk_forward else backtester.run(symbol, days)

        return self._worker.submit(execute).result()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._worker.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            if self._backend is not None:
                self._backend.close()
                self._backend = None
