from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from execution.base_backend import BackendStatus, BaseBackend


class CLIBackend(BaseBackend):
    name = "cli"

    def __init__(self, binary: str | None = None) -> None:
        self.binary = binary or shutil.which("okx")

    def _run(self, args: list[str]) -> dict[str, Any]:
        if not self.binary:
            raise RuntimeError("OKX_CLI_NOT_CONFIGURED")
        completed = subprocess.run([self.binary, "--demo", "--json", *args], capture_output=True, text=True, timeout=20)
        if completed.returncode != 0:
            raise RuntimeError(f"OKX_CLI_ERROR:{completed.stderr[-300:]}")
        value = json.loads(completed.stdout)
        return {"data": value} if isinstance(value, list) else value

    def status(self) -> BackendStatus:
        if not self.binary:
            return BackendStatus(self.name, "NOT_CONFIGURED", False, False, "OKX_CLI_NOT_CONFIGURED")
        try:
            self._run(["market", "ticker", "BTC-USDT"])
            return BackendStatus(self.name, "PARTIALLY_WORKING", True, True, "PUBLIC_MARKET_VERIFIED;PRIVATE_NOT_SELECTED")
        except Exception as exc:
            return BackendStatus(self.name, "UNAVAILABLE", False, False, str(exc))

    def get_ticker(self, symbol: str) -> dict[str, Any]: return self._run(["market", "ticker", symbol])
    def get_candles(self, symbol: str, timeframe: str, limit: int = 100, after: str | None = None, before: str | None = None) -> dict[str, Any]:
        if after or before:
            raise RuntimeError("CLI_HISTORICAL_PAGINATION_NOT_ENABLED")
        return self._run(["market", "candles", symbol, "--bar", timeframe, "--limit", str(limit)])
    def get_orderbook(self, symbol: str, depth: int = 5) -> dict[str, Any]:
        return self._run(["market", "orderbook", symbol, "--sz", str(depth)])
    def get_instrument(self, symbol: str) -> dict[str, Any]:
        return self._run(["market", "instruments", "--instType", "SPOT", "--instId", symbol])
    def get_account(self) -> dict[str, Any]: return self._run(["account", "balance"])
    def get_open_orders(self, symbol: str | None = None) -> dict[str, Any]:
        args = ["spot", "orders", "--status", "open"] + (["--instId", symbol] if symbol else [])
        return self._run(args)
    def get_fills(self, symbol: str | None = None) -> dict[str, Any]:
        return self._run(["spot", "fills"] + (["--instId", symbol] if symbol else []))
    def get_order(self, symbol: str, order_id: str) -> dict[str, Any]:
        return self._run(["spot", "order", "--instId", symbol, "--ordId", order_id])
    def place_order(self, order: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("CLI_EXECUTION_NOT_ENABLED")
    def cancel_order(self, symbol: str, order_id: str) -> dict[str, Any]:
        raise RuntimeError("CLI_EXECUTION_NOT_ENABLED")
