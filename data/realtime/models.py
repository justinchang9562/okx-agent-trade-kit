from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from data.models import Instrument


class MarketStreamState(str, Enum):
    CONNECTED = "CONNECTED"
    RESYNCING = "RESYNCING"
    STALE = "STALE"
    DISCONNECTED = "DISCONNECTED"


class MarketSource(str, Enum):
    REALTIME_WS = "REALTIME_WS"
    POLLING_FALLBACK = "POLLING_FALLBACK"


@dataclass
class SymbolMarketState:
    symbol: str
    stream_state: MarketStreamState = MarketStreamState.DISCONNECTED
    source: MarketSource = MarketSource.REALTIME_WS
    last_price: float | None = None
    bid: float | None = None
    ask: float | None = None
    volume_24h: float = 0.0
    exchange_timestamp_ms: int | None = None
    receive_timestamp_ms: int | None = None
    updated_timestamp_ms: int | None = None
    last_book_sequence: int | None = None
    last_book_timestamp_ms: int | None = None
    instrument: Instrument | None = None
    public_connected: bool = False
    business_connected: bool = False
    reconnect_count: int = 0
    stale_count: int = 0
    duplicate_messages_dropped: int = 0
    out_of_order_messages: int = 0
    validation_error: str | None = None
    confirmed_candle_timestamps: dict[str, int | None] = field(
        default_factory=lambda: {"1m": None, "3m": None, "5m": None}
    )

    @property
    def spread(self) -> float | None:
        return self.ask - self.bid if self.ask is not None and self.bid is not None else None

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["stream_state"] = self.stream_state.value
        value["source"] = self.source.value
        return value


@dataclass(frozen=True)
class ConfirmedCandleEvent:
    symbol: str
    timeframe: str
    candle_timestamp_ms: int
    received_timestamp_ms: int
