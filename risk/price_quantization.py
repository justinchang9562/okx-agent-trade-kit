from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal


def _quantize_to_tick(value: float, tick_size: float, rounding: str) -> float:
    if value <= 0 or tick_size <= 0:
        raise ValueError("INVALID_PRICE_OR_TICK_SIZE")
    price = Decimal(str(value))
    tick = Decimal(str(tick_size))
    units = (price / tick).to_integral_value(rounding=rounding)
    return float(units * tick)


def quantize_long_execution_prices(
    entry: float, stop: float, take_profit: float, tick_size: float,
) -> tuple[float, float, float]:
    """Conservative long rounding: entry/stop up, target down, then RR must be rechecked."""
    final_entry = _quantize_to_tick(entry, tick_size, ROUND_CEILING)
    final_stop = _quantize_to_tick(stop, tick_size, ROUND_CEILING)
    final_target = _quantize_to_tick(take_profit, tick_size, ROUND_FLOOR)
    if not (0 < final_stop < final_entry < final_target):
        raise ValueError("INVALID_QUANTIZED_PROTECTION_PRICES")
    return final_entry, final_stop, final_target


def risk_reward(entry: float, stop: float, take_profit: float) -> float:
    risk = entry - stop
    if risk <= 0 or take_profit <= entry:
        raise ValueError("INVALID_FINAL_RISK_REWARD_PRICES")
    return (take_profit - entry) / risk
