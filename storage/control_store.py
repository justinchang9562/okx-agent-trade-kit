from __future__ import annotations

import json
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

from storage.database import connect
from storage.trade_store import now_ms
from trading_agent.control_state import (
    AgentRuntimeState,
    ConnectionState,
    ControlSnapshot,
    EnvironmentState,
    ExecutionState,
    LiveSetupState,
    TradingMode,
)


class ControlStore:
    """Persistent control/audit projection; startup always overwrites risky state."""

    def __init__(self, path: Path) -> None:
        self.connection = connect(path)
        self._lock = threading.RLock()

    def reset_for_startup(self) -> ControlSnapshot:
        safe = ControlSnapshot(updated_at_ms=now_ms())
        with self._lock:
            previous = self.get_optional()
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                self._write(safe, commit=False)
                self._audit(
                    previous or safe, safe, "SERVICE_STARTUP_SAFE_RESET", "system",
                    "SAFE_RESTART", commit=False,
                )
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise
        return safe

    def get_optional(self) -> ControlSnapshot | None:
        with self._lock:
            row = self.connection.execute("SELECT * FROM control_state WHERE id = 1").fetchone()
        if row is None:
            return None
        return ControlSnapshot(
            environment=EnvironmentState(row["environment"]),
            live_setup_state=LiveSetupState(row["live_setup_state"]),
            execution_state=ExecutionState(row["execution_state"]),
            agent_runtime_state=AgentRuntimeState(row["agent_runtime_state"]),
            trading_mode=TradingMode(row["trading_mode"]),
            connection_state=ConnectionState(row["connection_state"]),
            kill_switch_active=bool(row["kill_switch_active"]),
            auto_demo_enabled=bool(row["auto_demo_enabled"]),
            scan_interval_seconds=float(row["scan_interval_seconds"]),
            updated_at_ms=int(row["updated_at_ms"]),
        )

    def get(self) -> ControlSnapshot:
        return self.get_optional() or self.reset_for_startup()

    def transition(
        self, action: str, actor: str = "local-user", reason: str = "PASS", **changes: Any,
    ) -> ControlSnapshot:
        with self._lock:
            previous = self.get()
            current = replace(previous, **changes, updated_at_ms=now_ms())
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                self._write(current, commit=False)
                self._audit(previous, current, action, actor, reason, commit=False)
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise
            return current

    def list_audit(self, limit: int = 200) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 1000))
        with self._lock:
            rows = self.connection.execute(
                "SELECT * FROM control_audit_log ORDER BY timestamp_ms DESC, id DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _write(self, state: ControlSnapshot, *, commit: bool = True) -> None:
        self.connection.execute(
            """INSERT INTO control_state
               (id, environment, live_setup_state, execution_state, agent_runtime_state,
                trading_mode, connection_state, kill_switch_active, auto_demo_enabled,
                scan_interval_seconds, updated_at_ms)
               VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 environment=excluded.environment,
                 live_setup_state=excluded.live_setup_state,
                 execution_state=excluded.execution_state,
                 agent_runtime_state=excluded.agent_runtime_state,
                 trading_mode=excluded.trading_mode,
                 connection_state=excluded.connection_state,
                 kill_switch_active=excluded.kill_switch_active,
                 auto_demo_enabled=excluded.auto_demo_enabled,
                 scan_interval_seconds=excluded.scan_interval_seconds,
                 updated_at_ms=excluded.updated_at_ms""",
            (
                state.environment.value, state.live_setup_state.value,
                state.execution_state.value, state.agent_runtime_state.value,
                state.trading_mode.value, state.connection_state.value,
                int(state.kill_switch_active), int(state.auto_demo_enabled),
                state.scan_interval_seconds, state.updated_at_ms,
            ),
        )
        if commit:
            self.connection.commit()

    def _audit(
        self, previous: ControlSnapshot, current: ControlSnapshot,
        action: str, actor: str, reason: str, *, commit: bool = True,
    ) -> None:
        self.connection.execute(
            """INSERT INTO control_audit_log
               (timestamp_ms, actor, requested_action, previous_state_json,
                result_state_json, reason) VALUES (?, ?, ?, ?, ?, ?)""",
            (now_ms(), actor, action, json.dumps(previous.as_dict(), default=str),
             json.dumps(current.as_dict(), default=str), reason),
        )
        if commit:
            self.connection.commit()

    def close(self) -> None:
        with self._lock:
            self.connection.close()
