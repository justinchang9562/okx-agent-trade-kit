from __future__ import annotations

from dataclasses import dataclass

from data.models import AccountSnapshot, MarketSnapshot


@dataclass(frozen=True)
class ExposureSnapshot:
    wallet_exposure_usdt: float
    managed_exposure_usdt: float
    projected_total_exposure_usdt: float
    total_exposure_pct: float
    priced_currencies: tuple[str, ...] = ()
    unpriced_currencies: tuple[str, ...] = ()


def current_spot_exposure_usdt(account: AccountSnapshot, market: MarketSnapshot) -> float:
    return account.balance(market.instrument.base_currency) * market.price


def wallet_exposure_usdt(account: AccountSnapshot, prices: dict[str, float]) -> tuple[float, tuple[str, ...]]:
    stable = {"USDT", "USDC", "USD"}
    total = 0.0
    unpriced: list[str] = []
    for balance in account.balances:
        if balance.currency in stable or balance.equity <= 0:
            continue
        price = prices.get(balance.currency)
        if price is None:
            if balance.equity > 0:
                unpriced.append(balance.currency)
            continue
        total += balance.equity * price
    return total, tuple(sorted(unpriced))


def build_exposure_snapshot(
    account: AccountSnapshot,
    prices: dict[str, float],
    managed_notional_usdt: float,
    proposed_notional_usdt: float = 0.0,
) -> ExposureSnapshot:
    wallet_value, unpriced = wallet_exposure_usdt(account, prices)
    # Managed positions are already physically represented in spot wallet balances after fill.
    # Do not double count them in total account exposure.
    projected = wallet_value + max(0.0, proposed_notional_usdt)
    pct = projected / account.equity_usdt if account.equity_usdt > 0 else float("inf")
    return ExposureSnapshot(
        wallet_exposure_usdt=wallet_value,
        managed_exposure_usdt=max(0.0, managed_notional_usdt),
        projected_total_exposure_usdt=projected,
        total_exposure_pct=pct,
        priced_currencies=tuple(sorted(prices)),
        unpriced_currencies=unpriced,
    )


def count_material_spot_positions(account: AccountSnapshot, prices: dict[str, float] | None = None) -> int:
    """Wallet inventory count only; never use this as managed-open-position count."""
    prices = prices or {}
    stable = {"USDT", "USDC", "USD"}
    return sum(
        1 for balance in account.balances
        if balance.currency not in stable and balance.equity > 0
        and prices.get(balance.currency, 1.0) * balance.equity > 1.0
    )
