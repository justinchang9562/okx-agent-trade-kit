from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from data.models import Instrument
from execution.base_backend import BaseBackend


def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    value: Any = payload.get("data", payload)
    if isinstance(value, dict) and isinstance(value.get("data"), list):
        value = value["data"]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


@dataclass(frozen=True)
class InstrumentCacheEntry:
    instrument: Instrument
    fetched_at_ms: int


class InstrumentCache:
    """Thread-safe hours-scale metadata cache; stale cache never authorizes a plan."""

    def __init__(self, backend: BaseBackend, ttl_seconds: float = 4 * 60 * 60) -> None:
        if ttl_seconds <= 0:
            raise ValueError("INVALID_INSTRUMENT_CACHE_TTL")
        self.backend = backend
        self.ttl_ms = int(ttl_seconds * 1000)
        self._entries: dict[str, InstrumentCacheEntry] = {}
        self._lock = threading.RLock()
        self.request_count = 0

    def get(self, symbol: str, *, now_ms: int | None = None) -> Instrument:
        current = now_ms if now_ms is not None else int(time.time() * 1000)
        symbol = symbol.upper()
        with self._lock:
            cached = self._entries.get(symbol)
            if cached is not None and current - cached.fetched_at_ms <= self.ttl_ms:
                return cached.instrument
            try:
                self.request_count += 1
                rows = _rows(self.backend.get_instrument(symbol))
                row = rows[0]
                instrument = Instrument(
                    symbol=symbol,
                    base_currency=str(row["baseCcy"]),
                    quote_currency=str(row["quoteCcy"]),
                    min_size=float(row["minSz"]),
                    lot_size=float(row["lotSz"]),
                    tick_size=float(row["tickSz"]),
                )
                if min(instrument.min_size, instrument.lot_size, instrument.tick_size) <= 0:
                    raise ValueError("INVALID_INSTRUMENT_METADATA")
            except Exception as exc:
                raise RuntimeError("INSTRUMENT_METADATA_UNAVAILABLE") from exc
            entry = InstrumentCacheEntry(instrument, current)
            self._entries[symbol] = entry
            return instrument

    def prime(self, symbol: str, instrument: Instrument, *, fetched_at_ms: int | None = None) -> None:
        current = fetched_at_ms if fetched_at_ms is not None else int(time.time() * 1000)
        with self._lock:
            self._entries[symbol.upper()] = InstrumentCacheEntry(instrument, current)

    def status(self, symbol: str, *, now_ms: int | None = None) -> dict[str, Any]:
        current = now_ms if now_ms is not None else int(time.time() * 1000)
        with self._lock:
            entry = self._entries.get(symbol.upper())
            age = current - entry.fetched_at_ms if entry else None
            return {
                "available": entry is not None and age is not None and age <= self.ttl_ms,
                "age_ms": age,
                "ttl_ms": self.ttl_ms,
                "request_count": self.request_count,
            }
