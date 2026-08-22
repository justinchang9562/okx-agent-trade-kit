from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from data.models import Candle
from data.normalizer import normalize_candles
from execution.base_backend import BaseBackend


class HistoricalDataError(RuntimeError):
    pass


def interval_ms(timeframe: str) -> int:
    unit = timeframe[-1]
    amount = int(timeframe[:-1])
    multiplier = {"m": 60_000, "H": 3_600_000, "D": 86_400_000}.get(unit)
    if multiplier is None:
        raise HistoricalDataError(f"UNSUPPORTED_TIMEFRAME:{timeframe}")
    return amount * multiplier


@dataclass(frozen=True)
class IntegrityReport:
    symbol: str
    timeframe: str
    bars: int
    first_timestamp_ms: int | None
    last_timestamp_ms: int | None
    duplicates: int
    missing_bars: int
    unconfirmed_bars: int
    valid: bool
    status: str


def validate_history(symbol: str, timeframe: str, candles: tuple[Candle, ...]) -> IntegrityReport:
    timestamps = [item.timestamp_ms for item in candles]
    duplicates = len(timestamps) - len(set(timestamps))
    missing = 0
    step = interval_ms(timeframe)
    for previous, current in zip(timestamps, timestamps[1:]):
        if current - previous != step:
            missing += max(1, (current - previous) // step - 1)
    unconfirmed = sum(not item.confirmed for item in candles)
    ohlc_valid = all(
        min(item.open, item.high, item.low, item.close) > 0
        and item.high >= max(item.open, item.close, item.low)
        and item.low <= min(item.open, item.close, item.high)
        and item.volume >= 0
        for item in candles
    )
    ordered = timestamps == sorted(timestamps)
    valid = bool(candles) and not duplicates and not missing and not unconfirmed and ordered and ohlc_valid
    status = "PASS" if valid else "DATA_GAP" if missing or duplicates else "DATA_INVALID"
    return IntegrityReport(
        symbol, timeframe, len(candles), timestamps[0] if timestamps else None,
        timestamps[-1] if timestamps else None, duplicates, missing, unconfirmed, valid, status,
    )


class HistoricalDataService:
    def __init__(self, backend: BaseBackend, cache_root: Path, page_size: int = 100) -> None:
        self.backend = backend
        self.cache_root = cache_root
        self.page_size = min(100, max(1, page_size))

    def _path(self, symbol: str, timeframe: str, days: int) -> Path:
        return self.cache_root / f"{symbol.replace('-', '_')}_{timeframe}_{days}d.json"

    def _load_cache(self, path: Path) -> tuple[Candle, ...] | None:
        if not path.exists():
            return None
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))["candles"]
            return tuple(Candle(**row) for row in rows)
        except Exception:
            return None

    def _save_cache(self, path: Path, symbol: str, timeframe: str, days: int, candles: tuple[Candle, ...]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"symbol": symbol, "timeframe": timeframe, "days": days,
                   "candles": [asdict(item) for item in candles]}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        temporary.replace(path)

    def fetch(self, symbol: str, timeframe: str, days: int, use_cache: bool = True) -> tuple[tuple[Candle, ...], IntegrityReport]:
        if days not in {7, 30, 90}:
            raise HistoricalDataError("HISTORICAL_DAYS_MUST_BE_7_30_OR_90")
        target_count = days * 86_400_000 // interval_ms(timeframe)
        path = self._path(symbol, timeframe, days)
        cached = self._load_cache(path) if use_cache else None
        if cached and len(cached) >= target_count:
            selected = tuple(sorted(cached, key=lambda item: item.timestamp_ms)[-target_count:])
            report = validate_history(symbol, timeframe, selected)
            if report.valid:
                return selected, report
        collected: dict[int, Candle] = {item.timestamp_ms: item for item in (cached or ())}
        after: str | None = str(min(collected)) if collected else None
        previous_oldest: int | None = min(collected) if collected else None
        page_number = 0
        while len(collected) < target_count + 2:
            payload = None
            for attempt in range(5):
                try:
                    payload = self.backend.get_candles(symbol, timeframe, self.page_size, after=after)
                    break
                except Exception as exc:
                    if "429" not in str(exc) or attempt == 4:
                        if use_cache and collected:
                            self._save_cache(path, symbol, timeframe, days, tuple(sorted(collected.values(), key=lambda item: item.timestamp_ms)))
                        raise HistoricalDataError(f"DATA_UNAVAILABLE:PAGINATION:{type(exc).__name__}") from exc
                    time.sleep(min(8.0, 2.0 ** attempt))
            if payload is None:
                raise HistoricalDataError("DATA_UNAVAILABLE:PAGINATION_EMPTY_RESPONSE")
            page = normalize_candles(payload)
            if not page:
                break
            for candle in page:
                if candle.confirmed:
                    collected[candle.timestamp_ms] = candle
            oldest = min(item.timestamp_ms for item in page)
            if previous_oldest is not None and oldest >= previous_oldest:
                break
            previous_oldest = oldest
            after = str(oldest)
            page_number += 1
            if use_cache and page_number % 10 == 0:
                self._save_cache(
                    path, symbol, timeframe, days,
                    tuple(sorted(collected.values(), key=lambda item: item.timestamp_ms)),
                )
        ordered = tuple(sorted(collected.values(), key=lambda item: item.timestamp_ms))
        if len(ordered) < target_count:
            raise HistoricalDataError(f"DATA_UNAVAILABLE:requested={target_count}:received={len(ordered)}")
        selected = ordered[-target_count:]
        report = validate_history(symbol, timeframe, selected)
        if report.missing_bars:
            step = interval_ms(timeframe)
            gaps = [(left.timestamp_ms, right.timestamp_ms) for left, right in zip(selected, selected[1:])
                    if right.timestamp_ms - left.timestamp_ms != step]
            if len(gaps) <= 20:
                for _left, right in gaps:
                    try:
                        repair = normalize_candles(
                            self.backend.get_candles(
                                symbol, timeframe, min(self.page_size, 20), after=str(right + step)
                            )
                        )
                    except Exception:
                        continue
                    for candle in repair:
                        if candle.confirmed:
                            collected[candle.timestamp_ms] = candle
                ordered = tuple(sorted(collected.values(), key=lambda item: item.timestamp_ms))
                selected = ordered[-target_count:]
                report = validate_history(symbol, timeframe, selected)
        if not report.valid:
            raise HistoricalDataError(f"{report.status}:{asdict(report)}")
        if use_cache:
            self._save_cache(path, symbol, timeframe, days, selected)
        return selected, report
