from __future__ import annotations

from typing import Any

from execution.base_backend import BackendStatus, BaseBackend


class NativeAPIBackend(BaseBackend):
    name = "native_api"

    def status(self) -> BackendStatus:
        return BackendStatus(self.name, "NOT_CONFIGURED", False, False, "NATIVE_API_NOT_CONFIGURED")

    def _unavailable(self, *args: object, **kwargs: object) -> dict[str, Any]:
        raise RuntimeError("NATIVE_API_NOT_CONFIGURED")

    get_ticker = get_candles = get_orderbook = get_instrument = get_account = _unavailable
    get_open_orders = get_fills = get_order = place_order = cancel_order = _unavailable
