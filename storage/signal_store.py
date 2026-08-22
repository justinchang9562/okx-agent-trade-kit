from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from decision.trade_plan import TradePlan
from storage.database import connect
from strategies.signal import Signal


class SignalStore:
    def __init__(self, path: Path) -> None:
        self.connection = connect(path)
        self._lock = threading.RLock()

    def record(self, signal: Signal, plan: TradePlan) -> None:
        with self._lock:
            self.connection.execute(
                "INSERT INTO signals (timestamp_ms, symbol, score, confidence, signal_strength, decision, reasons_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (signal.timestamp_ms, signal.symbol, signal.score, signal.signal_strength,
                 signal.signal_strength, plan.decision, json.dumps(signal.reasons)),
            )
            if plan.decision == "REJECT":
                self.connection.execute(
                    "INSERT INTO rejected_signals (timestamp_ms, symbol, reason, signal_score) VALUES (?, ?, ?, ?)",
                    (signal.timestamp_ms, signal.symbol, plan.risk_status, signal.score),
                )
            self.connection.commit()

    def list_signals(self, limit: int = 200) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 1000))
        with self._lock:
            rows = self.connection.execute(
                """SELECT timestamp_ms, symbol, score, signal_strength, decision, reasons_json
                     FROM signals ORDER BY timestamp_ms DESC, id DESC LIMIT ?""",
                (safe_limit,),
            ).fetchall()
        return [dict(row) | {"reasons": json.loads(row["reasons_json"])} for row in rows]

    def close(self) -> None:
        with self._lock:
            self.connection.close()
