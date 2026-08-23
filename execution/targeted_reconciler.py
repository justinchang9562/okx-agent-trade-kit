from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any


class TargetedOrderReconciler:
    """Bounded, single-order reconciliation used only after submit/cancel writes."""

    def __init__(
        self,
        delays_seconds: tuple[float, ...] = (0.0, 0.3, 1.0, 2.0),
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not delays_seconds or any(delay < 0 for delay in delays_seconds):
            raise ValueError("INVALID_TARGETED_RECONCILIATION_SCHEDULE")
        self.delays_seconds = delays_seconds
        self.sleeper = sleeper

    def run(
        self,
        lookup: Callable[[], dict[str, Any]],
        complete: Callable[[dict[str, Any]], bool],
    ) -> dict[str, Any]:
        last: dict[str, Any] = {"found": False, "reason": "RECONCILIATION_REQUIRED"}
        for attempt, delay in enumerate(self.delays_seconds, start=1):
            if delay:
                self.sleeper(delay)
            try:
                last = lookup()
            except Exception as exc:
                last = {"found": False, "reason": type(exc).__name__}
            last = dict(last) | {"targeted_reconciliation_attempts": attempt}
            if complete(last):
                return last
        return last | {"reason": last.get("reason") or "RECONCILIATION_REQUIRED"}
