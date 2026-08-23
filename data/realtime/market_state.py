from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from data.models import Candle, Instrument, MarketSnapshot
from data.realtime.candle_buffer import TIMEFRAME_MS, CandleBuffer
from data.realtime.metrics import RealtimeMetrics
from data.realtime.models import ConfirmedCandleEvent, MarketSource, MarketStreamState, SymbolMarketState


class RealtimeMarketError(RuntimeError):
    """Raised when the realtime market state cannot safely authorize a new entry."""


class RealtimeMarketState:
    def __init__(
        self,
        symbols: tuple[str, ...],
        *,
        minimum_candles: int = 50,
        stale_after_seconds: float = 5.0,
        capacity: int = 500,
        sequence_regression_resync_threshold: int = 1_000,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self.symbols = tuple(item.upper() for item in symbols)
        self.minimum_candles = minimum_candles
        self.stale_after_ms = int(stale_after_seconds * 1000)
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self.sequence_regression_resync_threshold = sequence_regression_resync_threshold
        self.metrics = RealtimeMetrics()
        self._states = {symbol: SymbolMarketState(symbol) for symbol in self.symbols}
        self._buffers = {
            symbol: {
                timeframe: CandleBuffer(timeframe, capacity=capacity, minimum=minimum_candles)
                for timeframe in TIMEFRAME_MS
            }
            for symbol in self.symbols
        }
        self._lock = threading.RLock()

    def bootstrap_symbol(
        self,
        symbol: str,
        candles: dict[str, tuple[Candle, ...]],
        instrument: Instrument,
    ) -> None:
        symbol = self._require_symbol(symbol)
        with self._lock:
            for timeframe, buffer in self._buffers[symbol].items():
                buffer.bootstrap(candles[timeframe])
                last = buffer.last_confirmed
                newest = buffer.snapshot()[-1]
                if newest.timestamp_ms > self.clock_ms() + 5_000:
                    raise RealtimeMarketError(f"DATA_INVALID:{timeframe}:future candle")
                self._states[symbol].confirmed_candle_timestamps[timeframe] = (
                    last.timestamp_ms if last else None
                )
            self._states[symbol].instrument = instrument
            # A historical bootstrap validates candles and the REST snapshot, but a
            # reconnect must still receive a fresh websocket book before entries are
            # authorized.  Never let a sequence/timestamp from the previous socket
            # promote the new session to CONNECTED.
            self._states[symbol].last_book_sequence = None
            self._states[symbol].last_book_timestamp_ms = None
            self._states[symbol].stream_state = MarketStreamState.RESYNCING
            self._states[symbol].validation_error = None

    def seed_market(
        self,
        symbol: str,
        *,
        price: float,
        bid: float,
        ask: float,
        volume_24h: float,
        exchange_timestamp_ms: int,
        receive_timestamp_ms: int | None = None,
    ) -> None:
        """Seed reconnect validation from a read-only bootstrap; it never marks CONNECTED."""
        symbol = self._require_symbol(symbol)
        received = receive_timestamp_ms if receive_timestamp_ms is not None else self.clock_ms()
        if (
            min(price, bid, ask) <= 0
            or ask < bid
            or exchange_timestamp_ms <= 0
            or exchange_timestamp_ms > received + 5_000
        ):
            raise RealtimeMarketError("DATA_INVALID:bootstrap market")
        with self._lock:
            state = self._states[symbol]
            state.last_price = price
            state.bid = bid
            state.ask = ask
            state.volume_24h = volume_24h
            state.exchange_timestamp_ms = exchange_timestamp_ms
            state.receive_timestamp_ms = received
            state.updated_timestamp_ms = received

    def mark_resyncing(self) -> None:
        with self._lock:
            for state in self._states.values():
                state.stream_state = MarketStreamState.RESYNCING
                state.public_connected = False
                state.business_connected = False

    def mark_disconnected(self, endpoint_kind: str | None = None) -> None:
        with self._lock:
            for state in self._states.values():
                if endpoint_kind == "public":
                    state.public_connected = False
                elif endpoint_kind == "business":
                    state.business_connected = False
                else:
                    state.public_connected = False
                    state.business_connected = False
                state.stream_state = MarketStreamState.DISCONNECTED

    def mark_endpoint_connected(self, endpoint_kind: str) -> None:
        if endpoint_kind not in {"public", "business"}:
            raise ValueError("INVALID_ENDPOINT_KIND")
        with self._lock:
            for state in self._states.values():
                if endpoint_kind == "public":
                    state.public_connected = True
                else:
                    state.business_connected = True
                self._promote_if_ready(state)

    def record_reconnect(self) -> None:
        self.metrics.increment("reconnect_count")
        with self._lock:
            for state in self._states.values():
                state.reconnect_count += 1

    def apply_message(self, payload: dict[str, Any], receive_timestamp_ms: int | None = None) -> list[ConfirmedCandleEvent]:
        received = receive_timestamp_ms if receive_timestamp_ms is not None else self.clock_ms()
        if not isinstance(payload, dict):
            raise RealtimeMarketError("INVALID_WEBSOCKET_PAYLOAD")
        if payload.get("event"):
            if payload.get("event") == "error":
                raise RealtimeMarketError("WEBSOCKET_SUBSCRIPTION_ERROR")
            return []
        arg = payload.get("arg")
        rows = payload.get("data")
        if not isinstance(arg, dict) or not isinstance(rows, list) or not rows:
            raise RealtimeMarketError("INVALID_WEBSOCKET_PAYLOAD")
        channel = str(arg.get("channel", ""))
        symbol = self._require_symbol(str(arg.get("instId", "")))
        self.metrics.increment("websocket_messages")
        if channel == "tickers":
            self._apply_ticker(symbol, rows[0], received)
            return []
        if channel == "books5":
            self._apply_book(symbol, rows[0], received)
            return []
        if channel.startswith("candle"):
            timeframe = channel.removeprefix("candle")
            if timeframe not in TIMEFRAME_MS:
                raise RealtimeMarketError("UNSUPPORTED_CANDLE_CHANNEL")
            return self._apply_candles(symbol, timeframe, rows, received)
        raise RealtimeMarketError("UNSUPPORTED_WEBSOCKET_CHANNEL")

    def _apply_ticker(self, symbol: str, row: Any, received: int) -> None:
        if not isinstance(row, dict):
            raise RealtimeMarketError("INVALID_TICKER_PAYLOAD")
        try:
            timestamp = int(row["ts"])
            price = float(row["last"])
            bid = float(row["bidPx"])
            ask = float(row["askPx"])
            volume = float(row.get("vol24h") or 0)
        except (KeyError, TypeError, ValueError) as exc:
            raise RealtimeMarketError("INVALID_TICKER_PAYLOAD") from exc
        if min(price, bid, ask) <= 0 or ask < bid or timestamp <= 0 or timestamp > received + 5_000:
            raise RealtimeMarketError("INVALID_TICKER_VALUES")
        with self._lock:
            state = self._states[symbol]
            if state.exchange_timestamp_ms is not None and timestamp < state.exchange_timestamp_ms:
                state.out_of_order_messages += 1
                self.metrics.increment("out_of_order_events")
                return
            if state.exchange_timestamp_ms == timestamp and state.last_price == price and state.bid == bid and state.ask == ask:
                state.duplicate_messages_dropped += 1
                self.metrics.increment("duplicate_events_dropped")
                return
            state.last_price, state.bid, state.ask = price, bid, ask
            state.volume_24h = volume
            state.exchange_timestamp_ms = timestamp
            state.receive_timestamp_ms = received
            state.updated_timestamp_ms = self.clock_ms()
            state.validation_error = None
            self.metrics.record_market(timestamp, received, state.updated_timestamp_ms)
            self._promote_if_ready(state)

    def _apply_book(self, symbol: str, row: Any, received: int) -> None:
        if not isinstance(row, dict):
            raise RealtimeMarketError("INVALID_ORDERBOOK_PAYLOAD")
        try:
            timestamp = int(row["ts"])
            sequence = int(row["seqId"])
            bids, asks = row["bids"], row["asks"]
            bid = float(bids[0][0])
            ask = float(asks[0][0])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RealtimeMarketError("INVALID_ORDERBOOK_PAYLOAD") from exc
        if (
            bid <= 0
            or ask <= 0
            or ask < bid
            or timestamp <= 0
            or timestamp > received + 5_000
            or sequence < 0
        ):
            raise RealtimeMarketError("INVALID_ORDERBOOK_VALUES")
        with self._lock:
            state = self._states[symbol]
            if state.last_book_sequence is not None and sequence < state.last_book_sequence:
                state.out_of_order_messages += 1
                self.metrics.increment("out_of_order_events")
                if state.last_book_sequence - sequence >= self.sequence_regression_resync_threshold:
                    state.stream_state = MarketStreamState.RESYNCING
                    state.public_connected = False
                    state.last_book_sequence = None
                    state.last_book_timestamp_ms = None
                    state.validation_error = "BOOK_SEQUENCE_REGRESSION"
                    raise RealtimeMarketError("BOOK_SEQUENCE_REGRESSION_RESYNC_REQUIRED")
                return
            if state.last_book_sequence == sequence and state.bid == bid and state.ask == ask:
                state.duplicate_messages_dropped += 1
                self.metrics.increment("duplicate_events_dropped")
                return
            state.bid, state.ask = bid, ask
            state.last_book_sequence = sequence
            state.last_book_timestamp_ms = timestamp
            state.receive_timestamp_ms = received
            state.updated_timestamp_ms = self.clock_ms()
            state.validation_error = None
            self.metrics.record_market(timestamp, received, state.updated_timestamp_ms)
            self._promote_if_ready(state)

    def _apply_candles(
        self, symbol: str, timeframe: str, rows: list[Any], received: int
    ) -> list[ConfirmedCandleEvent]:
        events: list[ConfirmedCandleEvent] = []
        with self._lock:
            state = self._states[symbol]
            for row in rows:
                try:
                    if not isinstance(row, list) or len(row) < 9:
                        raise ValueError
                    candle = Candle(
                        timestamp_ms=int(row[0]),
                        open=float(row[1]),
                        high=float(row[2]),
                        low=float(row[3]),
                        close=float(row[4]),
                        volume=float(row[5]),
                        quote_volume=float(row[7] or 0),
                        confirmed=str(row[8]) == "1",
                    )
                    if candle.timestamp_ms > received + 5_000:
                        raise ValueError
                except (TypeError, ValueError) as exc:
                    raise RealtimeMarketError("INVALID_CANDLE_PAYLOAD") from exc
                update = self._buffers[symbol][timeframe].apply(candle)
                if update.duplicate:
                    state.duplicate_messages_dropped += 1
                    self.metrics.increment("duplicate_events_dropped")
                if update.out_of_order:
                    state.out_of_order_messages += 1
                    self.metrics.increment("out_of_order_events")
                if update.missing_intervals:
                    state.validation_error = "MISSING_CANDLE_INTERVAL"
                    state.stream_state = MarketStreamState.STALE
                    state.stale_count += 1
                    self.metrics.increment("stale_count")
                if not update.accepted:
                    continue
                if candle.confirmed:
                    state.confirmed_candle_timestamps[timeframe] = candle.timestamp_ms
                if update.confirmed_event:
                    self.metrics.timestamps["confirmed_candle_timestamp"] = candle.timestamp_ms
                    events.append(ConfirmedCandleEvent(symbol, timeframe, candle.timestamp_ms, received))
            state.updated_timestamp_ms = self.clock_ms()
            self._promote_if_ready(state)
        return events

    def _promote_if_ready(self, state: SymbolMarketState) -> None:
        if not state.public_connected or not state.business_connected:
            return
        if state.instrument is None or min(state.last_price or 0, state.bid or 0, state.ask or 0) <= 0:
            return
        if state.ask is not None and state.bid is not None and state.ask < state.bid:
            return
        if all(buffer.complete for buffer in self._buffers[state.symbol].values()):
            state.stream_state = MarketStreamState.CONNECTED
            state.source = MarketSource.REALTIME_WS
            state.validation_error = None

    def watchdog(self, now_ms: int | None = None) -> tuple[str, ...]:
        current = now_ms if now_ms is not None else self.clock_ms()
        stale: list[str] = []
        with self._lock:
            for symbol, state in self._states.items():
                invalid = (
                    state.stream_state is not MarketStreamState.CONNECTED
                    or state.exchange_timestamp_ms is None
                    or state.last_book_timestamp_ms is None
                )
                # OKX tickers and books5 are event-driven: an unchanged market may
                # legitimately emit no new symbol-level quote for several seconds.
                # Transport liveness is enforced by the public/business connection
                # state and the WebSocket ping/pong timeout. Confirmed candles below
                # remain channel-specific freshness evidence. Treating quote exchange
                # timestamps as a fixed 5-second heartbeat causes false fail-closed
                # degradation on quieter symbols such as SOL Demo.
                for timeframe, buffer in self._buffers[symbol].items():
                    last = buffer.last_confirmed
                    if last is None or current - (last.timestamp_ms + TIMEFRAME_MS[timeframe]) > (
                        TIMEFRAME_MS[timeframe] + self.stale_after_ms
                    ):
                        invalid = True
                if invalid:
                    if state.stream_state is MarketStreamState.CONNECTED:
                        state.stale_count += 1
                        self.metrics.increment("stale_count")
                    if state.stream_state is not MarketStreamState.DISCONNECTED:
                        state.stream_state = MarketStreamState.STALE
                    stale.append(symbol)
        return tuple(stale)

    def get_snapshot(self, symbol: str, *, now_ms: int | None = None) -> MarketSnapshot:
        symbol = self._require_symbol(symbol)
        current = now_ms if now_ms is not None else self.clock_ms()
        self.watchdog(current)
        with self._lock:
            state = self._states[symbol]
            if state.stream_state is not MarketStreamState.CONNECTED:
                raise RealtimeMarketError(f"MARKET_STREAM_{state.stream_state.value}")
            if state.instrument is None or state.last_price is None or state.bid is None or state.ask is None:
                raise RealtimeMarketError("MARKET_SNAPSHOT_INCOMPLETE")
            candles = {name: buffer.snapshot() for name, buffer in self._buffers[symbol].items()}
            return MarketSnapshot(
                symbol=symbol,
                timestamp_ms=int(state.exchange_timestamp_ms or 0),
                price=state.last_price,
                bid=state.bid,
                ask=state.ask,
                volume_24h=state.volume_24h,
                candles=candles,
                instrument=state.instrument,
            )

    def assert_entry_ready(self, symbol: str | None = None) -> None:
        targets = (self._require_symbol(symbol),) if symbol else self.symbols
        self.watchdog()
        with self._lock:
            for target in targets:
                if self._states[target].stream_state is not MarketStreamState.CONNECTED:
                    raise PermissionError(f"MARKET_STREAM_{self._states[target].stream_state.value}")

    def status(self) -> dict[str, Any]:
        self.watchdog()
        with self._lock:
            return {
                "source": MarketSource.REALTIME_WS.value,
                "symbols": {symbol: state.as_dict() for symbol, state in self._states.items()},
                "metrics": self.metrics.snapshot(),
            }

    def _require_symbol(self, symbol: str) -> str:
        normalized = symbol.upper()
        if normalized not in self._states:
            raise RealtimeMarketError(f"SYMBOL_NOT_CONFIGURED:{normalized}")
        return normalized
