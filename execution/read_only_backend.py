from __future__ import annotations

from typing import Any

from execution.base_backend import BackendStatus, BaseBackend


class ReadOnlyBackend(BaseBackend):
    """Capability-preserving backend view that makes all writes impossible."""

    name = "read-only"

    def __init__(self, backend: BaseBackend) -> None:
        self._backend = backend
        self.name = f"{backend.name}-read-only"

    def status(self) -> BackendStatus:
        return self._backend.status()

    def get_ticker(self, symbol: str) -> dict[str, Any]:
        return self._backend.get_ticker(symbol)

    def get_candles(
        self, symbol: str, timeframe: str, limit: int = 100,
        after: str | None = None, before: str | None = None,
    ) -> dict[str, Any]:
        return self._backend.get_candles(symbol, timeframe, limit, after, before)

    def get_orderbook(self, symbol: str, depth: int = 5) -> dict[str, Any]:
        return self._backend.get_orderbook(symbol, depth)

    def get_instrument(self, symbol: str) -> dict[str, Any]:
        return self._backend.get_instrument(symbol)

    def get_account(self) -> dict[str, Any]:
        return self._backend.get_account()

    def get_open_orders(self, symbol: str | None = None) -> dict[str, Any]:
        return self._backend.get_open_orders(symbol)

    def get_fills(
        self,
        symbol: str | None = None,
        *,
        after: str | None = None,
        before: str | None = None,
        order_id: str | None = None,
        begin_ms: int | None = None,
        end_ms: int | None = None,
        archive: bool = False,
        limit: int = 100,
    ) -> dict[str, Any]:
        return self._backend.get_fills(
            symbol,
            after=after,
            before=before,
            order_id=order_id,
            begin_ms=begin_ms,
            end_ms=end_ms,
            archive=archive,
            limit=limit,
        )

    def get_order(self, symbol: str, order_id: str) -> dict[str, Any]:
        return self._backend.get_order(symbol, order_id)

    def get_order_by_client_id(self, symbol: str, client_order_id: str) -> dict[str, Any]:
        return self._backend.get_order_by_client_id(symbol, client_order_id)

    def get_protection_orders(self, symbol: str | None = None) -> dict[str, Any]:
        return self._backend.get_protection_orders(symbol)

    def capabilities(self) -> dict[str, Any]:
        return self._backend.capabilities() | {"write_operations": False}

    def place_order(self, order: dict[str, Any]) -> dict[str, Any]:
        raise PermissionError("READ_ONLY_BACKEND")

    def cancel_order(self, symbol: str, order_id: str) -> dict[str, Any]:
        raise PermissionError("READ_ONLY_BACKEND")

    def close(self) -> None:
        self._backend.close()
