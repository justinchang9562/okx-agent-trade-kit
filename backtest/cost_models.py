from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class FeeModel(Protocol):
    def cost(self, entry: float, exit: float, quantity: float) -> float: ...


class SpreadModel(Protocol):
    def half_spread_pct(self, timestamp_ms: int) -> float: ...


class SlippageModel(Protocol):
    def pct(self, timestamp_ms: int, side: str) -> float: ...


@dataclass(frozen=True)
class FixedFeeModel:
    fee_pct: float

    def cost(self, entry: float, exit: float, quantity: float) -> float:
        return (entry + exit) * quantity * self.fee_pct


@dataclass(frozen=True)
class FixedSpreadModel:
    spread_pct: float

    def half_spread_pct(self, timestamp_ms: int) -> float:
        return self.spread_pct / 2


@dataclass(frozen=True)
class ObservedSpreadModel:
    observations: dict[int, float]
    fallback_pct: float

    def half_spread_pct(self, timestamp_ms: int) -> float:
        return self.observations.get(timestamp_ms, self.fallback_pct) / 2


@dataclass(frozen=True)
class FixedSlippageModel:
    slippage_pct: float

    def pct(self, timestamp_ms: int, side: str) -> float:
        return self.slippage_pct


@dataclass(frozen=True)
class EmpiricalSlippageModel:
    observations: tuple[float, ...]
    fallback_pct: float

    def pct(self, timestamp_ms: int, side: str) -> float:
        if not self.observations:
            return self.fallback_pct
        ordered = sorted(self.observations)
        return ordered[len(ordered) // 2]
