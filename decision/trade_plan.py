from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any

from execution.order_state import OrderState


@dataclass(frozen=True)
class TradePlan:
    plan_id: str
    symbol: str
    environment: str
    backend: str
    side: str
    current_price: float
    entry: float
    stop: float | None
    take_profit: float | None
    position_size: float
    estimated_usdt: float
    risk_amount: float
    risk_pct: float
    risk_reward: float | None
    signal_score: int
    signal_strength: float
    reasons: tuple[str, ...]
    risk_status: str
    risk_approved: bool
    decision: str
    timestamp_ms: int
    strategy: str
    created_at_ms: int
    expires_at_ms: int
    status: str = OrderState.PLANNED.value

    @classmethod
    def identifier(cls, symbol: str, side: str, strategy: str, timestamp_ms: int) -> str:
        bucket = timestamp_ms // 60_000
        return sha256(f"{symbol}|{side}|{strategy}|{bucket}".encode()).hexdigest()[:20]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def confidence(self) -> float:
        """Legacy compatibility only; never display this as win probability."""
        return self.signal_strength

    def is_expired(self, now_ms: int) -> bool:
        return now_ms >= self.expires_at_ms

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TradePlan":
        data = dict(value)
        if "signal_strength" not in data:
            data["signal_strength"] = data.pop("confidence")
        data["reasons"] = tuple(data.get("reasons", ()))
        data.setdefault("created_at_ms", data.get("timestamp_ms", 0))
        data.setdefault("expires_at_ms", data["created_at_ms"] + 60_000)
        data.setdefault("status", OrderState.PLANNED.value)
        data.pop("confidence", None)
        return cls(**data)
