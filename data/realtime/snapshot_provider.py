from __future__ import annotations

from typing import Protocol

from data.market_data import MarketDataService
from data.models import MarketSnapshot
from data.realtime.market_state import RealtimeMarketState


class MarketSnapshotProvider(Protocol):
    def get_snapshot(self, symbol: str) -> MarketSnapshot: ...


class PollingMarketSnapshotProvider:
    source = "POLLING_FALLBACK"

    def __init__(self, service: MarketDataService) -> None:
        self.service = service

    def get_snapshot(self, symbol: str) -> MarketSnapshot:
        return self.service.get_snapshot(symbol)


class RealtimeMarketSnapshotProvider:
    source = "REALTIME_WS"

    def __init__(self, state: RealtimeMarketState) -> None:
        self.state = state

    def get_snapshot(self, symbol: str) -> MarketSnapshot:
        return self.state.get_snapshot(symbol)
