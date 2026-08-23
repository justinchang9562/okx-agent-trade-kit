from __future__ import annotations

import math
import threading
from collections import deque
from typing import Any


def _percentile(values: tuple[float, ...], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


class RealtimeMetrics:
    def __init__(self, window: int = 2048) -> None:
        self._market_latency_ms: deque[float] = deque(maxlen=window)
        self._strategy_latency_ms: deque[float] = deque(maxlen=window)
        self._lock = threading.RLock()
        self.counters: dict[str, int] = {
            "websocket_messages": 0,
            "duplicate_events_dropped": 0,
            "out_of_order_events": 0,
            "reconnect_count": 0,
            "stale_count": 0,
            "strategy_evaluations": 0,
            "trade_plans_generated": 0,
            "critical_queue_overflows": 0,
            "historical_bootstrap_requests": 0,
            "instrument_requests": 0,
        }
        self.timestamps: dict[str, int | None] = {
            "exchange_message_timestamp": None,
            "local_receive_timestamp": None,
            "market_state_updated_timestamp": None,
            "confirmed_candle_timestamp": None,
            "strategy_start_timestamp": None,
            "strategy_end_timestamp": None,
            "trade_plan_created_timestamp": None,
            "approval_timestamp": None,
            "submit_timestamp": None,
        }

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self.counters[name] = self.counters.get(name, 0) + amount

    def record_market(self, exchange_ms: int, receive_ms: int, updated_ms: int) -> None:
        with self._lock:
            self._market_latency_ms.append(max(0.0, float(receive_ms - exchange_ms)))
            self.timestamps.update(
                exchange_message_timestamp=exchange_ms,
                local_receive_timestamp=receive_ms,
                market_state_updated_timestamp=updated_ms,
            )

    def record_strategy(self, start_ms: int, end_ms: int, plan_created: bool) -> None:
        with self._lock:
            self._strategy_latency_ms.append(max(0.0, float(end_ms - start_ms)))
            self.timestamps["strategy_start_timestamp"] = start_ms
            self.timestamps["strategy_end_timestamp"] = end_ms
            self.counters["strategy_evaluations"] += 1
            if plan_created:
                self.counters["trade_plans_generated"] += 1
                self.timestamps["trade_plan_created_timestamp"] = end_ms

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            market = tuple(self._market_latency_ms)
            strategy = tuple(self._strategy_latency_ms)
            return {
                **self.timestamps,
                **self.counters,
                "market_latency_current_ms": market[-1] if market else None,
                "market_latency_average_ms": sum(market) / len(market) if market else None,
                "market_latency_p95_ms": _percentile(market, 0.95),
                "strategy_latency_last_ms": strategy[-1] if strategy else None,
                "strategy_latency_average_ms": sum(strategy) / len(strategy) if strategy else None,
                "strategy_latency_p95_ms": _percentile(strategy, 0.95),
            }
