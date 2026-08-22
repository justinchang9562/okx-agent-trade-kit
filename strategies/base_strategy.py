from __future__ import annotations

from abc import ABC, abstractmethod

from data.models import MarketSnapshot
from strategies.signal import Signal


class BaseStrategy(ABC):
    @abstractmethod
    def analyze(self, market: MarketSnapshot) -> Signal:
        raise NotImplementedError
