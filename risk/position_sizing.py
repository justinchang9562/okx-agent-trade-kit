from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN

from data.models import AccountSnapshot, Instrument


@dataclass(frozen=True)
class SizingResult:
    approved: bool
    reason: str
    quantity: float = 0.0
    notional_usdt: float = 0.0
    risk_amount: float = 0.0
    capped: bool = False


def _floor_step(value: float, step: float) -> float:
    if step <= 0:
        raise ValueError("lot size must be positive")
    units = (Decimal(str(value)) / Decimal(str(step))).to_integral_value(rounding=ROUND_DOWN)
    return float(units * Decimal(str(step)))


def calculate_position_size(
    account: AccountSnapshot,
    instrument: Instrument,
    entry: float,
    stop: float,
    risk_per_trade: float,
    max_position_pct: float,
) -> SizingResult:
    if account.equity_usdt <= 0 or account.available_usdt <= 0:
        return SizingResult(False, "ACCOUNT_FUNDS_UNAVAILABLE")
    stop_distance = entry - stop
    if entry <= 0 or stop <= 0 or stop_distance <= 0:
        return SizingResult(False, "INVALID_STOP_DISTANCE")
    risk_amount = account.equity_usdt * risk_per_trade
    risk_quantity = risk_amount / stop_distance
    cap_notional = min(account.equity_usdt * max_position_pct, account.available_usdt)
    cap_quantity = cap_notional / entry
    raw_quantity = min(risk_quantity, cap_quantity)
    quantity = _floor_step(raw_quantity, instrument.lot_size)
    notional = quantity * entry
    if quantity < instrument.min_size:
        return SizingResult(False, "BELOW_MINIMUM_ORDER_SIZE", risk_amount=risk_amount)
    return SizingResult(
        True, "PASS", quantity=quantity, notional_usdt=notional, risk_amount=quantity * stop_distance,
        capped=cap_quantity < risk_quantity,
    )
