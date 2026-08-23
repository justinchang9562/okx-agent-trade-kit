from __future__ import annotations

from dataclasses import dataclass

from data.models import Candle

TIMEFRAME_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000}


@dataclass(frozen=True)
class CandleUpdate:
    accepted: bool
    confirmed_event: bool = False
    duplicate: bool = False
    out_of_order: bool = False
    missing_intervals: int = 0
    reason: str = "PASS"


class CandleBuffer:
    """Bounded rolling candle buffer with deterministic merge and confirmation semantics."""

    def __init__(self, timeframe: str, capacity: int = 500, minimum: int = 50) -> None:
        if timeframe not in TIMEFRAME_MS:
            raise ValueError(f"UNSUPPORTED_TIMEFRAME:{timeframe}")
        if capacity < minimum or minimum < 1:
            raise ValueError("INVALID_CANDLE_BUFFER_LIMITS")
        self.timeframe = timeframe
        self.interval_ms = TIMEFRAME_MS[timeframe]
        self.capacity = capacity
        self.minimum = minimum
        self._candles: dict[int, Candle] = {}
        self._emitted_confirmed: set[int] = set()
        self.complete = False
        self.missing_intervals = 0

    def bootstrap(self, candles: tuple[Candle, ...]) -> None:
        ordered = sorted(candles, key=lambda item: item.timestamp_ms)
        if not ordered:
            raise ValueError(f"DATA_INVALID:{self.timeframe}:empty bootstrap")
        timestamps = [item.timestamp_ms for item in ordered]
        if len(timestamps) != len(set(timestamps)):
            raise ValueError(f"DATA_INVALID:{self.timeframe}:duplicate bootstrap")
        if any(item.timestamp_ms % self.interval_ms for item in ordered):
            raise ValueError(f"DATA_INVALID:{self.timeframe}:unaligned candle")
        confirmed = [item for item in ordered if item.confirmed]
        if len(confirmed) < self.minimum:
            raise ValueError(f"DATA_INVALID:{self.timeframe}:minimum candles")
        gaps = sum(
            max(0, (current.timestamp_ms - previous.timestamp_ms) // self.interval_ms - 1)
            for previous, current in zip(ordered, ordered[1:])
        )
        if gaps:
            raise ValueError(f"DATA_INVALID:{self.timeframe}:missing intervals")
        self._candles = {item.timestamp_ms: item for item in ordered[-self.capacity:]}
        self._emitted_confirmed = {item.timestamp_ms for item in ordered if item.confirmed}
        self.missing_intervals = 0
        self.complete = True

    def apply(self, candle: Candle) -> CandleUpdate:
        if candle.timestamp_ms <= 0 or candle.timestamp_ms % self.interval_ms:
            return CandleUpdate(False, reason="INVALID_CANDLE_TIMESTAMP")
        if min(candle.open, candle.high, candle.low, candle.close) <= 0 or candle.volume < 0:
            return CandleUpdate(False, reason="INVALID_CANDLE_VALUE")
        if candle.high < max(candle.open, candle.close, candle.low) or candle.low > min(
            candle.open, candle.close, candle.high
        ):
            return CandleUpdate(False, reason="INVALID_CANDLE_OHLC")

        existing = self._candles.get(candle.timestamp_ms)
        newest = max(self._candles) if self._candles else None
        if existing is not None:
            if existing == candle:
                return CandleUpdate(False, duplicate=True, reason="DUPLICATE_CANDLE")
            if existing.confirmed:
                return CandleUpdate(False, out_of_order=True, reason="CONFIRMED_CANDLE_MUTATION")
            self._candles[candle.timestamp_ms] = candle
            event = candle.confirmed and candle.timestamp_ms not in self._emitted_confirmed
            if event:
                self._emitted_confirmed.add(candle.timestamp_ms)
            return CandleUpdate(True, confirmed_event=event)

        if newest is not None and candle.timestamp_ms < newest:
            return CandleUpdate(False, out_of_order=True, reason="OUT_OF_ORDER_CANDLE")
        missing = 0
        if newest is not None and candle.timestamp_ms > newest + self.interval_ms:
            missing = (candle.timestamp_ms - newest) // self.interval_ms - 1
            self.missing_intervals += missing
            self.complete = False
        self._candles[candle.timestamp_ms] = candle
        while len(self._candles) > self.capacity:
            del self._candles[min(self._candles)]
        event = candle.confirmed and candle.timestamp_ms not in self._emitted_confirmed
        if event:
            self._emitted_confirmed.add(candle.timestamp_ms)
        return CandleUpdate(True, confirmed_event=event, missing_intervals=missing)

    def snapshot(self) -> tuple[Candle, ...]:
        return tuple(self._candles[key] for key in sorted(self._candles))

    @property
    def last_confirmed(self) -> Candle | None:
        confirmed = [item for item in self._candles.values() if item.confirmed]
        return max(confirmed, key=lambda item: item.timestamp_ms) if confirmed else None
