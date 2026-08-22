from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from data.models import AccountSnapshot, Balance, Candle, Fill, Instrument, MarketSnapshot, Order


class NormalizationError(RuntimeError):
    """Raised when an external backend response cannot be normalized safely."""


def _items(payload: dict[str, Any]) -> list[Any]:
    data = payload.get("data", payload)
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        return data["data"]
    if isinstance(data, list):
        return data
    raise NormalizationError("DATA_UNAVAILABLE:unexpected OKX response")


def normalize_candles(payload: dict[str, Any]) -> tuple[Candle, ...]:
    candles = []
    for row in _items(payload):
        if not isinstance(row, list) or len(row) < 6:
            raise NormalizationError("DATA_INVALID:malformed candle")
        candles.append(Candle(
            timestamp_ms=int(row[0]), open=float(row[1]), high=float(row[2]),
            low=float(row[3]), close=float(row[4]), volume=float(row[5]),
            quote_volume=float(row[7]) if len(row) > 7 and row[7] else 0.0,
            confirmed=(str(row[8]) == "1") if len(row) > 8 else True,
        ))
    return tuple(sorted(candles, key=lambda candle: candle.timestamp_ms))


def normalize_market(
    symbol: str,
    ticker_payload: dict[str, Any],
    orderbook_payload: dict[str, Any],
    instrument_payload: dict[str, Any],
    candles: dict[str, tuple[Candle, ...]],
) -> MarketSnapshot:
    ticker = _items(ticker_payload)[0]
    book = _items(orderbook_payload)[0]
    meta = _items(instrument_payload)[0]
    bid = float(book["bids"][0][0]) if book.get("bids") else float(ticker["bidPx"])
    ask = float(book["asks"][0][0]) if book.get("asks") else float(ticker["askPx"])
    instrument = Instrument(
        symbol=symbol, base_currency=str(meta["baseCcy"]), quote_currency=str(meta["quoteCcy"]),
        min_size=float(meta["minSz"]), lot_size=float(meta["lotSz"]), tick_size=float(meta["tickSz"]),
    )
    return MarketSnapshot(
        symbol=symbol, timestamp_ms=int(ticker["ts"]), price=float(ticker["last"]),
        bid=bid, ask=ask, volume_24h=float(ticker.get("vol24h", 0)), candles=candles,
        instrument=instrument,
    )


def _normalize_order(row: dict[str, Any]) -> Order:
    price_text = row.get("px") or row.get("avgPx")
    return Order(
        order_id=str(row.get("ordId", "")), client_order_id=str(row.get("clOrdId", "")),
        symbol=str(row.get("instId", "")), side=str(row.get("side", "")),
        order_type=str(row.get("ordType", "")), state=str(row.get("state", "")),
        size=float(row.get("sz") or 0), price=float(price_text) if price_text else None,
        timestamp_ms=int(row.get("cTime") or row.get("uTime") or 0),
        filled_size=float(row.get("accFillSz") or row.get("fillSz") or 0),
        average_fill_price=float(row["avgPx"]) if row.get("avgPx") not in (None, "") else None,
    )


def _normalize_fill(row: dict[str, Any]) -> Fill:
    return Fill(
        fill_id=str(row.get("tradeId") or row.get("billId") or ""), order_id=str(row.get("ordId", "")),
        symbol=str(row.get("instId", "")), side=str(row.get("side", "")),
        size=float(row.get("fillSz") or row.get("sz") or 0), price=float(row.get("fillPx") or row.get("px") or 0),
        fee=float(row["fee"]) if row.get("fee") not in (None, "") else None,
        timestamp_ms=int(row.get("ts") or row.get("fillTime") or 0),
        fee_currency=str(row.get("feeCcy")) if row.get("feeCcy") else None,
    )


def normalize_account(
    balance_payload: dict[str, Any], orders_payload: dict[str, Any], fills_payload: dict[str, Any]
) -> AccountSnapshot:
    outer = balance_payload.get("data", balance_payload)
    trading = outer.get("trading", {}) if isinstance(outer, dict) else {}
    if not trading.get("available", False):
        raise NormalizationError("DATA_UNAVAILABLE:trading account")
    balances_list = []
    for row in trading.get("details", []):
        equity = float(row.get("eq") or 0)
        frozen = float(row.get("frozenBal") or 0)
        available_text = row.get("availBal") or row.get("availEq")
        available = float(available_text) if available_text not in (None, "") else max(0.0, equity - frozen)
        balances_list.append(Balance(
            currency=str(row.get("ccy", "")).upper(), equity=equity,
            available=available, frozen=frozen,
        ))
    balances = tuple(balances_list)
    usdt = next((item for item in balances if item.currency == "USDT"), None)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return AccountSnapshot(
        timestamp_ms=now_ms, equity_usdt=float(trading.get("totalEq") or 0),
        available_usdt=usdt.available if usdt else 0.0, balances=balances,
        open_orders=tuple(_normalize_order(row) for row in _items(orders_payload)),
        fills=tuple(_normalize_fill(row) for row in _items(fills_payload)),
    )
