from __future__ import annotations

from typing import Any

PUBLIC_DEMO_ENDPOINT = "wss://wspap.okx.com:8443/ws/v5/public"
BUSINESS_DEMO_ENDPOINT = "wss://wspap.okx.com:8443/ws/v5/business"
SUPPORTED_TIMEFRAMES = ("1m", "3m", "5m")


def public_subscriptions(symbols: tuple[str, ...]) -> list[dict[str, str]]:
    return [
        {"channel": channel, "instId": symbol}
        for symbol in symbols
        for channel in ("tickers", "books5")
    ]


def business_subscriptions(symbols: tuple[str, ...]) -> list[dict[str, str]]:
    return [
        {"channel": f"candle{timeframe}", "instId": symbol}
        for symbol in symbols
        for timeframe in SUPPORTED_TIMEFRAMES
    ]


def subscribe_payload(arguments: list[dict[str, str]], request_id: str) -> dict[str, Any]:
    normalized_id = "".join(
        character for character in request_id
        if character.isascii() and character.isalnum()
    )[:32]
    if not normalized_id:
        raise ValueError("OKX_WEBSOCKET_REQUEST_ID_INVALID")
    return {"id": normalized_id, "op": "subscribe", "args": arguments}
