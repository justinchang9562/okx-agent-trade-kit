from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Candle:
    timestamp_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float = 0.0
    confirmed: bool = True


@dataclass(frozen=True)
class Instrument:
    symbol: str
    base_currency: str
    quote_currency: str
    min_size: float
    lot_size: float
    tick_size: float


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    timestamp_ms: int
    price: float
    bid: float
    ask: float
    volume_24h: float
    candles: dict[str, tuple[Candle, ...]]
    instrument: Instrument
    received_at: datetime = field(default_factory=utc_now)

    @property
    def spread_pct(self) -> float:
        midpoint = (self.bid + self.ask) / 2
        return ((self.ask - self.bid) / midpoint) * 100 if midpoint > 0 else float("inf")


@dataclass(frozen=True)
class Balance:
    currency: str
    equity: float
    available: float
    frozen: float = 0.0


@dataclass(frozen=True)
class Order:
    order_id: str
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    state: str
    size: float
    price: float | None
    timestamp_ms: int
    filled_size: float = 0.0
    average_fill_price: float | None = None


@dataclass(frozen=True)
class Fill:
    fill_id: str
    order_id: str
    symbol: str
    side: str
    size: float
    price: float
    fee: float | None
    timestamp_ms: int
    fee_currency: str | None = None


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: float
    value_usdt: float


@dataclass(frozen=True)
class AccountSnapshot:
    timestamp_ms: int
    equity_usdt: float
    available_usdt: float
    balances: tuple[Balance, ...]
    open_orders: tuple[Order, ...] = ()
    fills: tuple[Fill, ...] = ()
    daily_pnl: float | None = None

    def balance(self, currency: str) -> float:
        target = currency.upper()
        return next((b.equity for b in self.balances if b.currency == target), 0.0)


def to_dict(value: Any) -> dict[str, Any]:
    return asdict(value)
