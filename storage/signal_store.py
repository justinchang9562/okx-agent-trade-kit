from __future__ import annotations

import json
from pathlib import Path

from decision.trade_plan import TradePlan
from storage.database import connect
from strategies.signal import Signal


class SignalStore:
    def __init__(self, path: Path) -> None:
        self.connection = connect(path)

    def record(self, signal: Signal, plan: TradePlan) -> None:
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

    def close(self) -> None:
        self.connection.close()
