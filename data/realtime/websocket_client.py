from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from typing import Any

import websockets

from data.realtime.market_state import RealtimeMarketState
from data.realtime.models import ConfirmedCandleEvent
from data.realtime.subscriptions import (
    BUSINESS_DEMO_ENDPOINT,
    PUBLIC_DEMO_ENDPOINT,
    business_subscriptions,
    public_subscriptions,
    subscribe_payload,
)

ConnectFactory = Callable[[str], Any]


class OKXPublicWebSocketClient:
    """One event-loop thread managing the OKX Demo public and business feeds."""

    def __init__(
        self,
        symbols: tuple[str, ...],
        state: RealtimeMarketState,
        *,
        on_confirmed_candle: Callable[[ConfirmedCandleEvent], None],
        resync: Callable[[], None],
        connect_factory: ConnectFactory | None = None,
        heartbeat_seconds: float = 20.0,
        pong_timeout_seconds: float = 10.0,
        max_backoff_seconds: float = 30.0,
    ) -> None:
        self.symbols = symbols
        self.state = state
        self.on_confirmed_candle = on_confirmed_candle
        self.resync = resync
        self.connect_factory = connect_factory or websockets.connect
        self.heartbeat_seconds = heartbeat_seconds
        self.pong_timeout_seconds = pong_timeout_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._thread_main, name="okx-public-ws", daemon=True)
        self._thread.start()

    def _thread_main(self) -> None:
        asyncio.run(self.run())

    async def run(self) -> None:
        backoff = 1.0
        first = True
        while not self._stop.is_set():
            if not first:
                self.state.record_reconnect()
            first = False
            self.state.mark_resyncing()
            try:
                await asyncio.to_thread(self.resync)
                tasks = {
                    asyncio.create_task(self._endpoint_loop(
                        PUBLIC_DEMO_ENDPOINT, "public", public_subscriptions(self.symbols)
                    )),
                    asyncio.create_task(self._endpoint_loop(
                        BUSINESS_DEMO_ENDPOINT, "business", business_subscriptions(self.symbols)
                    )),
                }
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    exception = task.exception()
                    if exception is not None:
                        raise exception
                backoff = 1.0
            except asyncio.CancelledError:
                return
            except Exception:
                self.state.mark_disconnected()
                if self._stop.is_set():
                    return
                await asyncio.sleep(backoff)
                backoff = min(self.max_backoff_seconds, backoff * 2)

    async def _endpoint_loop(
        self, endpoint: str, kind: str, arguments: list[dict[str, str]]
    ) -> None:
        connector = self.connect_factory(endpoint)
        async with connector as websocket:
            await websocket.send(json.dumps(subscribe_payload(arguments, f"v041-{kind}")))
            pending_acks = {(item["channel"], item["instId"]) for item in arguments}
            pong_deadline: float | None = None
            loop = asyncio.get_running_loop()
            while not self._stop.is_set():
                timeout = self.heartbeat_seconds
                if pong_deadline is not None:
                    timeout = max(0.0, pong_deadline - loop.time())
                try:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=timeout)
                except TimeoutError:
                    if pong_deadline is not None:
                        raise ConnectionError("OKX_WEBSOCKET_PONG_TIMEOUT")
                    await websocket.send("ping")
                    pong_deadline = loop.time() + self.pong_timeout_seconds
                    continue
                if raw == "pong":
                    pong_deadline = None
                    continue
                if not isinstance(raw, str):
                    raise ValueError("OKX_WEBSOCKET_NON_TEXT_MESSAGE")
                message = json.loads(raw)
                if message.get("event") == "subscribe":
                    arg = message.get("arg", {})
                    pending_acks.discard((str(arg.get("channel", "")), str(arg.get("instId", ""))))
                    if not pending_acks:
                        self.state.mark_endpoint_connected(kind)
                    continue
                if message.get("event") in {"error", "notice"}:
                    raise ConnectionError(f"OKX_WEBSOCKET_{str(message.get('event')).upper()}")
                for event in self.state.apply_message(message):
                    if event.timeframe == "1m":
                        self.on_confirmed_candle(event)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self.state.mark_disconnected()
