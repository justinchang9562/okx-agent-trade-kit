from __future__ import annotations

from data.models import AccountSnapshot
from data.normalizer import normalize_account
from execution.base_backend import BaseBackend


class AccountDataService:
    def __init__(self, backend: BaseBackend) -> None:
        self.backend = backend

    def get_snapshot(self, symbol: str | None = None) -> AccountSnapshot:
        return normalize_account(
            self.backend.get_account(), self.backend.get_open_orders(symbol), self.backend.get_fills(symbol)
        )
