from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from data.normalizer import normalize_candles
from data.realtime.instrument_cache import InstrumentCache
from data.realtime.market_state import RealtimeMarketState
from data.realtime.models import ConfirmedCandleEvent
from data.realtime.snapshot_provider import RealtimeMarketSnapshotProvider
from data.realtime.websocket_client import OKXPublicWebSocketClient
from execution.base_backend import BaseBackend


def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    value: Any = payload.get("data", payload)
    if isinstance(value, dict) and isinstance(value.get("data"), list):
        value = value["data"]
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


class RealtimeMarketRuntime:
    def __init__(
        self,
        backend: BaseBackend,
        symbols: tuple[str, ...],
        rules: dict[str, Any],
        instrument_cache: InstrumentCache,
        on_confirmed_candle: Callable[[ConfirmedCandleEvent], None],
        *,
        client_factory: Callable[..., OKXPublicWebSocketClient] = OKXPublicWebSocketClient,
    ) -> None:
        market_rules = rules.get("realtime", {})
        self.backend = backend
        self.symbols = symbols
        self.rules = rules
        self.instrument_cache = instrument_cache
        self.state = RealtimeMarketState(
            symbols,
            minimum_candles=int(rules["market"]["minimum_candles"]),
            stale_after_seconds=float(market_rules.get("stale_after_seconds", 5)),
            capacity=int(market_rules.get("candle_capacity", 500)),
        )
        self.provider = RealtimeMarketSnapshotProvider(self.state)
        self.bootstrap_limit = int(market_rules.get("bootstrap_candles", 200))
        self.client = client_factory(
            symbols,
            self.state,
            on_confirmed_candle=on_confirmed_candle,
            resync=self.bootstrap,
        )

    def bootstrap(self) -> None:
        """Read-only historical bootstrap used only at startup and reconnect."""
        for symbol in self.symbols:
            frames = {}
            for timeframe in ("1m", "3m", "5m"):
                self.state.metrics.increment("historical_bootstrap_requests")
                frames[timeframe] = normalize_candles(
                    self.backend.get_candles(symbol, timeframe, self.bootstrap_limit)
                )
            before = self.instrument_cache.request_count
            instrument = self.instrument_cache.get(symbol)
            self.state.metrics.increment("instrument_requests", self.instrument_cache.request_count - before)
            ticker_rows = _rows(self.backend.get_ticker(symbol))
            book_rows = _rows(self.backend.get_orderbook(symbol, 5))
            if not ticker_rows or not book_rows:
                raise RuntimeError("REALTIME_BOOTSTRAP_MARKET_UNAVAILABLE")
            ticker, book = ticker_rows[0], book_rows[0]
            bids, asks = book.get("bids", []), book.get("asks", [])
            if not bids or not asks:
                raise RuntimeError("REALTIME_BOOTSTRAP_BOOK_UNAVAILABLE")
            self.state.bootstrap_symbol(symbol, frames, instrument)
            self.state.seed_market(
                symbol,
                price=float(ticker["last"]),
                bid=float(bids[0][0]),
                ask=float(asks[0][0]),
                volume_24h=float(ticker.get("vol24h") or 0),
                exchange_timestamp_ms=int(ticker["ts"]),
                receive_timestamp_ms=int(time.time() * 1000),
            )

    def start(self) -> None:
        self.client.start()

    def watchdog(self) -> tuple[str, ...]:
        return self.state.watchdog()

    def status(self) -> dict[str, Any]:
        return self.state.status()

    def close(self) -> None:
        self.client.close()
