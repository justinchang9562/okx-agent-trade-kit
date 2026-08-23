from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BackendStatus:
    name: str
    state: str
    available: bool
    demo: bool
    reason: str = ""


class BaseBackend(ABC):
    name = "base"

    @abstractmethod
    def status(self) -> BackendStatus:
        raise NotImplementedError

    @abstractmethod
    def get_ticker(self, symbol: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_candles(
        self, symbol: str, timeframe: str, limit: int = 100,
        after: str | None = None, before: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_orderbook(self, symbol: str, depth: int = 5) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_instrument(self, symbol: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_account(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_open_orders(self, symbol: str | None = None) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
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
        raise NotImplementedError

    @abstractmethod
    def get_order(self, symbol: str, order_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def get_order_by_client_id(self, symbol: str, client_order_id: str) -> dict[str, Any]:
        raise NotImplementedError("CLIENT_ORDER_LOOKUP_NOT_SUPPORTED")

    def get_protection_orders(self, symbol: str | None = None) -> dict[str, Any]:
        raise NotImplementedError("TP_SL_BACKEND_NOT_SUPPORTED")

    def capabilities(self) -> dict[str, Any]:
        return {
            "client_order_id": False,
            "attached_tp_sl": False,
            "historical_pagination": False,
            "fills_pagination": False,
            "fill_order_lookup": False,
            "fills_time_window": False,
            "fills_archive": False,
            "concurrent_read_only": False,
        }

    @abstractmethod
    def place_order(self, order: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, symbol: str, order_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def close(self) -> None:
        return None
