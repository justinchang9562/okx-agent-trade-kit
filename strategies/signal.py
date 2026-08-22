from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Signal:
    symbol: str
    timestamp_ms: int
    side: str
    score: int
    signal_strength: float
    entry_price: float
    suggested_stop: float | None
    suggested_take_profit: float | None
    risk_reward: float | None
    reasons: tuple[str, ...] = ()
    timeframes: dict[str, str] = field(default_factory=dict)
    strategy: str = "rule_scalping_v1"

    @property
    def confidence(self) -> float:
        """Backward-compatible alias; this is not a probability of winning."""
        return self.signal_strength

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
