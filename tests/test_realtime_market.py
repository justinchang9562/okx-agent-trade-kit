from __future__ import annotations

import pytest

from data.models import Candle, Instrument
from data.realtime.candle_buffer import TIMEFRAME_MS
from data.realtime.market_state import RealtimeMarketError, RealtimeMarketState

NOW_MS = 1_800_000_000
SYMBOL = "BTC-USDT"


def candles(timeframe: str, *, count: int = 50) -> tuple[Candle, ...]:
    interval = TIMEFRAME_MS[timeframe]
    last_open = (NOW_MS // interval) * interval - interval
    first_open = last_open - ((count - 1) * interval)
    return tuple(
        Candle(
            timestamp_ms=first_open + (index * interval),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=10.0,
            quote_volume=1_000.0,
            confirmed=True,
        )
        for index in range(count)
    )


def ready_state() -> RealtimeMarketState:
    state = RealtimeMarketState(
        (SYMBOL,), minimum_candles=50, stale_after_seconds=5, clock_ms=lambda: NOW_MS,
    )
    state.bootstrap_symbol(
        SYMBOL,
        {timeframe: candles(timeframe) for timeframe in TIMEFRAME_MS},
        Instrument(SYMBOL, "BTC", "USDT", 0.00001, 0.00001, 0.1),
    )
    state.seed_market(
        SYMBOL,
        price=100.0,
        bid=99.9,
        ask=100.1,
        volume_24h=1_000.0,
        exchange_timestamp_ms=NOW_MS,
        receive_timestamp_ms=NOW_MS,
    )
    state.mark_endpoint_connected("public")
    state.mark_endpoint_connected("business")
    state.apply_message(
        {
            "arg": {"channel": "books5", "instId": SYMBOL},
            "data": [{
                "ts": str(NOW_MS), "seqId": 1,
                "bids": [["99.9", "1", "0", "1"]],
                "asks": [["100.1", "1", "0", "1"]],
            }],
        },
        NOW_MS,
    )
    state.assert_entry_ready(SYMBOL)
    return state


def test_confirmed_one_minute_candle_emits_once_and_drops_duplicate() -> None:
    state = ready_state()
    row = [str(NOW_MS), "100", "101", "99", "100.5", "10", "0", "1000", "1"]
    message = {
        "arg": {"channel": "candle1m", "instId": SYMBOL},
        "data": [row],
    }
    first = state.apply_message(message, NOW_MS)
    second = state.apply_message(message, NOW_MS)

    assert [(event.symbol, event.timeframe, event.candle_timestamp_ms) for event in first] == [
        (SYMBOL, "1m", NOW_MS),
    ]
    assert second == []
    metrics = state.status()["metrics"]
    assert metrics["duplicate_events_dropped"] == 1
    assert metrics["confirmed_candle_timestamp"] == NOW_MS


def test_reconnect_bootstrap_overlap_does_not_emit_old_confirmed_candle() -> None:
    state = ready_state()
    existing = candles("1m")[-1]
    events = state.apply_message(
        {
            "arg": {"channel": "candle1m", "instId": SYMBOL},
            "data": [[
                str(existing.timestamp_ms), str(existing.open), str(existing.high),
                str(existing.low), str(existing.close), str(existing.volume), "0",
                str(existing.quote_volume), "1",
            ]],
        },
        NOW_MS,
    )
    assert events == []


@pytest.mark.parametrize("endpoint", ["public", "business"])
def test_disconnect_and_stale_market_fail_closed_for_new_entries(endpoint) -> None:
    state = ready_state()
    state.mark_disconnected(endpoint)
    with pytest.raises(PermissionError, match="MARKET_STREAM_DISCONNECTED"):
        state.assert_entry_ready(SYMBOL)

    state = ready_state()
    assert state.watchdog(NOW_MS + TIMEFRAME_MS["1m"] + 5_001) == (SYMBOL,)
    with pytest.raises(PermissionError, match="MARKET_STREAM_STALE"):
        state.assert_entry_ready(SYMBOL)


def test_event_driven_quote_without_change_does_not_false_stale() -> None:
    state = ready_state()
    assert state.watchdog(NOW_MS + 5_001) == ()
    assert state.status()["symbols"][SYMBOL]["stream_state"] == "CONNECTED"


def test_reconnect_requires_fresh_websocket_book_after_full_bootstrap() -> None:
    state = ready_state()
    state.mark_resyncing()
    state.bootstrap_symbol(
        SYMBOL,
        {timeframe: candles(timeframe) for timeframe in TIMEFRAME_MS},
        Instrument(SYMBOL, "BTC", "USDT", 0.00001, 0.00001, 0.1),
    )
    state.seed_market(
        SYMBOL,
        price=100.0,
        bid=99.9,
        ask=100.1,
        volume_24h=1_000.0,
        exchange_timestamp_ms=NOW_MS,
        receive_timestamp_ms=NOW_MS,
    )
    state.mark_endpoint_connected("public")
    state.mark_endpoint_connected("business")
    with pytest.raises(PermissionError, match="MARKET_STREAM_(RESYNCING|STALE)"):
        state.assert_entry_ready(SYMBOL)

    state.apply_message(
        {
            "arg": {"channel": "books5", "instId": SYMBOL},
            "data": [{
                "ts": str(NOW_MS), "seqId": 2,
                "bids": [["99.9", "1", "0", "1"]],
                "asks": [["100.1", "1", "0", "1"]],
            }],
        },
        NOW_MS,
    )
    state.assert_entry_ready(SYMBOL)


def test_out_of_order_book_is_ignored_and_counted() -> None:
    state = ready_state()
    state.apply_message(
        {
            "arg": {"channel": "books5", "instId": SYMBOL},
            "data": [{
                "ts": str(NOW_MS), "seqId": 0,
                "bids": [["90", "1", "0", "1"]],
                "asks": [["91", "1", "0", "1"]],
            }],
        },
        NOW_MS,
    )
    status = state.status()
    assert status["symbols"][SYMBOL]["bid"] == 99.9
    assert status["metrics"]["out_of_order_events"] == 1


def test_future_dated_market_payload_is_rejected() -> None:
    state = ready_state()
    with pytest.raises(RealtimeMarketError, match="INVALID_ORDERBOOK_VALUES"):
        state.apply_message(
            {
                "arg": {"channel": "books5", "instId": SYMBOL},
                "data": [{
                    "ts": str(NOW_MS + 5_001), "seqId": 2,
                    "bids": [["99.9", "1", "0", "1"]],
                    "asks": [["100.1", "1", "0", "1"]],
                }],
            },
            NOW_MS,
        )


def test_large_book_sequence_regression_forces_resync() -> None:
    state = ready_state()
    state.apply_message(
        {
            "arg": {"channel": "books5", "instId": SYMBOL},
            "data": [{
                "ts": str(NOW_MS), "seqId": 5_000,
                "bids": [["99.8", "1", "0", "1"]],
                "asks": [["100.2", "1", "0", "1"]],
            }],
        },
        NOW_MS,
    )
    with pytest.raises(RealtimeMarketError, match="BOOK_SEQUENCE_REGRESSION"):
        state.apply_message(
            {
                "arg": {"channel": "books5", "instId": SYMBOL},
                "data": [{
                    "ts": str(NOW_MS), "seqId": 1,
                    "bids": [["99.9", "1", "0", "1"]],
                    "asks": [["100.1", "1", "0", "1"]],
                }],
            },
            NOW_MS,
        )
    with pytest.raises(PermissionError, match="MARKET_STREAM_(RESYNCING|STALE)"):
        state.assert_entry_ready(SYMBOL)
