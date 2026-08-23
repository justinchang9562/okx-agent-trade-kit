from __future__ import annotations

import asyncio
import json
from collections import deque

import pytest

from data.realtime.subscriptions import (
    BUSINESS_DEMO_ENDPOINT,
    PUBLIC_DEMO_ENDPOINT,
    business_subscriptions,
    public_subscriptions,
    subscribe_payload,
)
from data.realtime.websocket_client import OKXPublicWebSocketClient
from tests.test_realtime_market import NOW_MS, SYMBOL, ready_state

TIMEOUT = object()


class FakeWebSocket:
    def __init__(self, messages) -> None:
        self.messages = deque(messages)
        self.sent: list[str] = []
        self.on_empty = None

    async def send(self, value: str) -> None:
        self.sent.append(value)

    async def recv(self):
        if not self.messages:
            if self.on_empty:
                self.on_empty()
            return "pong"
        value = self.messages.popleft()
        if value is TIMEOUT:
            await asyncio.sleep(60)
        if isinstance(value, BaseException):
            raise value
        return value


class FakeConnector:
    def __init__(self, websocket: FakeWebSocket) -> None:
        self.websocket = websocket

    async def __aenter__(self):
        return self.websocket

    async def __aexit__(self, *_args):
        return False


def client_for(websocket: FakeWebSocket):
    state = ready_state()
    client = OKXPublicWebSocketClient(
        (SYMBOL,), state,
        on_confirmed_candle=lambda _event: None,
        resync=lambda: None,
        connect_factory=lambda _endpoint: FakeConnector(websocket),
        heartbeat_seconds=0.001,
        pong_timeout_seconds=0.05,
    )
    websocket.on_empty = client._stop.set
    return client, state


def ack(channel: str) -> str:
    return json.dumps({
        "event": "subscribe",
        "arg": {"channel": channel, "instId": SYMBOL},
    })


def test_subscribe_payload_and_all_ack_are_deterministic() -> None:
    arguments = public_subscriptions((SYMBOL,))
    websocket = FakeWebSocket([ack("tickers"), ack("books5")])
    client, state = client_for(websocket)
    state.mark_resyncing()
    asyncio.run(client._endpoint_loop("wss://fake", "public", arguments))
    assert json.loads(websocket.sent[0]) == subscribe_payload(arguments, "v041-public")
    assert state.status()["symbols"][SYMBOL]["public_connected"] is True


def test_partial_subscribe_ack_never_marks_endpoint_ready() -> None:
    arguments = public_subscriptions((SYMBOL,))
    websocket = FakeWebSocket([ack("tickers")])
    client, state = client_for(websocket)
    state.mark_resyncing()
    asyncio.run(client._endpoint_loop("wss://fake", "public", arguments))
    assert state.status()["symbols"][SYMBOL]["public_connected"] is False


def test_business_endpoint_uses_exact_candle_subscriptions_and_all_ack() -> None:
    arguments = business_subscriptions((SYMBOL,))
    websocket = FakeWebSocket([ack(item["channel"]) for item in arguments])
    client, state = client_for(websocket)
    state.mark_resyncing()
    asyncio.run(client._endpoint_loop("wss://fake", "business", arguments))
    assert json.loads(websocket.sent[0]) == subscribe_payload(arguments, "v041-business")
    assert state.status()["symbols"][SYMBOL]["business_connected"] is True


def test_business_message_between_ping_and_pong_is_processed() -> None:
    arguments = public_subscriptions((SYMBOL,))
    ticker = json.dumps({
        "arg": {"channel": "tickers", "instId": SYMBOL},
        "data": [{
            "ts": str(NOW_MS), "last": "101", "bidPx": "100.9",
            "askPx": "101.1", "vol24h": "1000",
        }],
    })
    websocket = FakeWebSocket([ack("tickers"), ack("books5"), TIMEOUT, ticker, "pong"])
    client, state = client_for(websocket)
    asyncio.run(client._endpoint_loop("wss://fake", "public", arguments))
    assert "ping" in websocket.sent
    assert state.status()["symbols"][SYMBOL]["last_price"] == 101.0


def test_candle_between_ping_and_pong_is_processed() -> None:
    arguments = business_subscriptions((SYMBOL,))
    candle = json.dumps({
        "arg": {"channel": "candle1m", "instId": SYMBOL},
        "data": [[str(NOW_MS), "100", "101", "99", "100.5", "10", "0", "1000", "1"]],
    })
    events = []
    websocket = FakeWebSocket([
        *(ack(item["channel"]) for item in arguments),
        TIMEOUT,
        candle,
        "pong",
    ])
    state = ready_state()
    client = OKXPublicWebSocketClient(
        (SYMBOL,),
        state,
        on_confirmed_candle=events.append,
        resync=lambda: None,
        connect_factory=lambda _endpoint: FakeConnector(websocket),
        heartbeat_seconds=0.001,
        pong_timeout_seconds=0.05,
    )
    websocket.on_empty = client._stop.set
    asyncio.run(client._endpoint_loop("wss://fake", "business", arguments))
    assert "ping" in websocket.sent
    assert [(item.timeframe, item.candle_timestamp_ms) for item in events] == [("1m", NOW_MS)]


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (json.dumps({"event": "error"}), "OKX_WEBSOCKET_ERROR"),
        (json.dumps({"event": "notice"}), "OKX_WEBSOCKET_NOTICE"),
    ],
)
def test_error_or_notice_forces_endpoint_failure(message, expected) -> None:
    websocket = FakeWebSocket([message])
    client, _state = client_for(websocket)
    with pytest.raises(ConnectionError, match=expected):
        asyncio.run(client._endpoint_loop("wss://fake", "public", public_subscriptions((SYMBOL,))))


def test_pong_timeout_fails_endpoint() -> None:
    websocket = FakeWebSocket([TIMEOUT, TIMEOUT])
    client, _state = client_for(websocket)
    with pytest.raises(ConnectionError, match="PONG_TIMEOUT"):
        asyncio.run(client._endpoint_loop("wss://fake", "public", public_subscriptions((SYMBOL,))))


@pytest.mark.parametrize("message", ["{bad-json", b"binary"])
def test_malformed_or_binary_payload_fails_endpoint(message) -> None:
    websocket = FakeWebSocket([message])
    client, _state = client_for(websocket)
    with pytest.raises((json.JSONDecodeError, ValueError)):
        asyncio.run(client._endpoint_loop("wss://fake", "public", public_subscriptions((SYMBOL,))))


def test_socket_close_propagates_to_reconnect_loop() -> None:
    websocket = FakeWebSocket([ConnectionError("socket closed")])
    client, _state = client_for(websocket)
    with pytest.raises(ConnectionError, match="socket closed"):
        asyncio.run(client._endpoint_loop("wss://fake", "public", public_subscriptions((SYMBOL,))))


def test_reconnect_resyncs_both_endpoints_and_bounds_exponential_backoff(
    monkeypatch,
) -> None:
    state = ready_state()
    resync_calls: list[int] = []
    endpoint_calls: list[tuple[str, str, list[dict[str, str]]]] = []
    sleeps: list[float] = []
    client = OKXPublicWebSocketClient(
        (SYMBOL,),
        state,
        on_confirmed_candle=lambda _event: None,
        resync=lambda: resync_calls.append(len(resync_calls) + 1),
        max_backoff_seconds=4.0,
    )

    async def failing_endpoint(endpoint, kind, arguments):
        endpoint_calls.append((endpoint, kind, arguments))
        if kind == "public":
            raise ConnectionError("forced reconnect")
        await asyncio.Future()

    async def fake_sleep(delay):
        sleeps.append(delay)
        if len(sleeps) == 4:
            client._stop.set()

    monkeypatch.setattr(client, "_endpoint_loop", failing_endpoint)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    asyncio.run(client.run())

    assert sleeps == [1.0, 2.0, 4.0, 4.0]
    assert len(resync_calls) == 4
    assert state.status()["metrics"]["reconnect_count"] == 3
    public = [item for item in endpoint_calls if item[1] == "public"]
    business = [item for item in endpoint_calls if item[1] == "business"]
    assert len(public) == len(business) == 4
    assert all(item[0] == PUBLIC_DEMO_ENDPOINT for item in public)
    assert all(item[0] == BUSINESS_DEMO_ENDPOINT for item in business)
    assert all(item[2] == public_subscriptions((SYMBOL,)) for item in public)
    assert all(item[2] == business_subscriptions((SYMBOL,)) for item in business)
