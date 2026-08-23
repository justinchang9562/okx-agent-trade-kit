from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any

from decision.trade_plan import TradePlan
from execution.errors import StateChangedError
from execution.order_state import (
    ACTIVE_ORDER_STATES,
    ALLOWED_TRANSITIONS,
    MANAGED_POSITION_STATES,
    OrderState,
)
from risk.daily_limits import DailyRiskState
from storage.database import connect


def now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def synchronized(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return locked


@dataclass(frozen=True)
class ManagedPosition:
    plan_id: str
    order_id: str | None
    symbol: str
    quantity: float
    entry_price: float | None
    state: str
    protection_state: str
    opened_at_ms: int
    updated_at_ms: int
    closed_at_ms: int | None = None
    exit_order_id: str | None = None
    protective_order_ids_json: str | None = None
    exit_filled_quantity: float = 0.0
    exit_fee_breakdown_json: str | None = None


class TradeStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection = connect(path)
        self._lock = threading.RLock()

    @synchronized
    def save_plan(self, plan: TradePlan) -> None:
        current = now_ms()
        self.connection.execute(
            """INSERT INTO trade_plans
               (plan_id, plan_json, symbol, status, created_at_ms, expires_at_ms, updated_at_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(plan_id) DO NOTHING""",
            (plan.plan_id, json.dumps(plan.as_dict(), default=str), plan.symbol, plan.status,
             plan.created_at_ms, plan.expires_at_ms, current),
        )
        self.connection.commit()

    @synchronized
    def update_pending_plan_snapshot(self, plan: TradePlan) -> bool:
        cursor = self.connection.execute(
            "UPDATE trade_plans SET plan_json = ?, updated_at_ms = ? WHERE plan_id = ? AND status = ?",
            (json.dumps(plan.as_dict(), default=str), now_ms(), plan.plan_id, OrderState.PLANNED.value),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    @synchronized
    def get_plan(self, plan_id: str) -> TradePlan | None:
        row = self.connection.execute(
            "SELECT plan_json, status, created_at_ms, expires_at_ms FROM trade_plans WHERE plan_id = ?", (plan_id,)
        ).fetchone()
        if row is None:
            return None
        value = json.loads(row["plan_json"])
        value["status"] = row["status"]
        value["created_at_ms"] = row["created_at_ms"]
        value["expires_at_ms"] = row["expires_at_ms"]
        return TradePlan.from_dict(value)

    @synchronized
    def list_pending_plans(self) -> list[dict[str, Any]]:
        current = now_ms()
        self.connection.execute(
            "UPDATE trade_plans SET status = ?, rejection_reason = 'PLAN_EXPIRED', updated_at_ms = ? "
            "WHERE status = ? AND expires_at_ms <= ?",
            (OrderState.REJECTED.value, current, OrderState.PLANNED.value, current),
        )
        self.connection.commit()
        rows = self.connection.execute(
            "SELECT plan_id, symbol, status, created_at_ms, expires_at_ms FROM trade_plans "
            "WHERE status = ? ORDER BY created_at_ms", (OrderState.PLANNED.value,)
        ).fetchall()
        return [dict(row) for row in rows]

    @synchronized
    def list_plans(self, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 1000))
        if status:
            rows = self.connection.execute(
                "SELECT * FROM trade_plans WHERE status = ? ORDER BY created_at_ms DESC LIMIT ?",
                (status, safe_limit),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM trade_plans ORDER BY created_at_ms DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        output: list[dict[str, Any]] = []
        current = now_ms()
        for row in rows:
            item = dict(row)
            plan = json.loads(item.pop("plan_json"))
            core_status = item["status"]
            ui_status = core_status
            if core_status == OrderState.PLANNED.value:
                ui_status = "EXPIRED" if int(item["expires_at_ms"]) <= current else "PENDING_APPROVAL"
            elif core_status == OrderState.REJECTED.value and item.get("rejection_reason") == "PLAN_EXPIRED":
                ui_status = "EXPIRED"
            output.append(plan | item | {"ui_status": ui_status})
        return output

    @synchronized
    def transition_plan(self, plan_id: str, expected: set[str], state: str, reason: str | None = None) -> bool:
        placeholders = ",".join("?" for _ in expected)
        cursor = self.connection.execute(
            f"UPDATE trade_plans SET status = ?, updated_at_ms = ?, rejection_reason = ? "
            f"WHERE plan_id = ? AND status IN ({placeholders})",
            (state, now_ms(), reason, plan_id, *sorted(expected)),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def reject_plan(self, plan_id: str, reason: str) -> bool:
        return self.transition_plan(plan_id, {OrderState.PLANNED.value}, OrderState.REJECTED.value, reason)

    @synchronized
    def is_duplicate(self, plan_id: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM order_lifecycle WHERE plan_id = ? UNION SELECT 1 FROM order_submissions WHERE plan_id = ?",
            (plan_id, plan_id),
        ).fetchone()
        return row is not None

    @synchronized
    def create_order(self, plan: TradePlan, client_order_id: str, expected_price: float) -> None:
        current = now_ms()
        self.connection.execute(
            """INSERT INTO order_lifecycle
               (plan_id, client_order_id, symbol, side, requested_size, state, created_at_ms,
                updated_at_ms, approved_at_ms, backend, environment, expected_execution_price)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (plan.plan_id, client_order_id, plan.symbol, plan.side, plan.position_size,
             OrderState.APPROVED.value, current, current, current, plan.backend, plan.environment,
             expected_price),
        )
        self.connection.commit()

    @synchronized
    def approve_and_create_order(self, plan: TradePlan, client_order_id: str, expected_price: float) -> bool:
        """Atomic approval/idempotency boundary; safe across concurrent duplicate approvals."""
        current = now_ms()
        with self._lock:
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                cursor = self.connection.execute(
                    "UPDATE trade_plans SET status = ?, updated_at_ms = ? WHERE plan_id = ? AND status = ?",
                    (OrderState.APPROVED.value, current, plan.plan_id, OrderState.PLANNED.value),
                )
                if cursor.rowcount != 1:
                    self.connection.rollback()
                    return False
                self.connection.execute(
                    """INSERT INTO order_lifecycle
                       (plan_id, client_order_id, symbol, side, requested_size, state, created_at_ms,
                        updated_at_ms, approved_at_ms, backend, environment, expected_execution_price)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (plan.plan_id, client_order_id, plan.symbol, plan.side, plan.position_size,
                     OrderState.APPROVED.value, current, current, current, plan.backend,
                     plan.environment, expected_price),
                )
                self.connection.commit()
                return True
            except Exception:
                self.connection.rollback()
                raise

    @synchronized
    def transition_order(
        self, plan_id: str, state: str, expected_state: str | None = None, **fields: Any,
    ) -> None:
        allowed = {
            "okx_order_id", "filled_size", "average_fill_price", "expected_execution_price",
            "slippage_abs", "slippage_pct", "fee", "fee_currency", "protection_state",
            "submitted_at_ms", "filled_at_ms", "closed_at_ms", "raw_response_json", "last_error",
        }
        invalid = set(fields).difference(allowed)
        if invalid:
            raise ValueError(f"INVALID_LIFECYCLE_FIELDS:{sorted(invalid)}")
        with self._lock:
            current_row = self.connection.execute(
                "SELECT state FROM order_lifecycle WHERE plan_id = ?", (plan_id,)
            ).fetchone()
            if current_row is None:
                raise LookupError("ORDER_LIFECYCLE_NOT_FOUND")
            current_state = str(current_row["state"])
            compare_state = expected_state or current_state
            if current_state != compare_state:
                raise StateChangedError("ORDER_STATE_CHANGED")
            if state not in ALLOWED_TRANSITIONS.get(compare_state, set()):
                raise RuntimeError(f"INVALID_ORDER_TRANSITION:{compare_state}->{state}")
            values = dict(fields)
            values.update(state=state, updated_at_ms=now_ms())
            assignments = ", ".join(f"{key} = ?" for key in values)
            cursor = self.connection.execute(
                f"UPDATE order_lifecycle SET {assignments} WHERE plan_id = ? AND state = ?",
                (*values.values(), plan_id, compare_state),
            )
            if cursor.rowcount != 1:
                self.connection.rollback()
                raise StateChangedError("ORDER_STATE_CHANGED")
            self.connection.execute(
                "UPDATE trade_plans SET status = ?, updated_at_ms = ? WHERE plan_id = ?",
                (state, now_ms(), plan_id),
            )
            self.connection.commit()

    @synchronized
    def order_for_plan(self, plan_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM order_lifecycle WHERE plan_id = ?", (plan_id,)).fetchone()
        return dict(row) if row else None

    @synchronized
    def active_orders(self) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in ACTIVE_ORDER_STATES)
        rows = self.connection.execute(
            f"SELECT * FROM order_lifecycle WHERE state IN ({placeholders}) ORDER BY created_at_ms",
            tuple(sorted(ACTIVE_ORDER_STATES)),
        ).fetchall()
        return [dict(row) for row in rows]

    @synchronized
    def upsert_managed_position(
        self, plan_id: str, order_id: str | None, symbol: str, quantity: float,
        entry_price: float | None, state: str, protection_state: str,
        protective_order_ids: list[str] | None = None,
    ) -> None:
        current = now_ms()
        self.connection.execute(
            """INSERT INTO managed_positions
               (plan_id, order_id, symbol, quantity, entry_price, state, protection_state,
                opened_at_ms, updated_at_ms, protective_order_ids_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(plan_id) DO UPDATE SET order_id=excluded.order_id,
                 quantity=excluded.quantity, entry_price=excluded.entry_price, state=excluded.state,
                 protection_state=excluded.protection_state, updated_at_ms=excluded.updated_at_ms,
                 protective_order_ids_json=COALESCE(excluded.protective_order_ids_json,
                                                     managed_positions.protective_order_ids_json)""",
            (plan_id, order_id, symbol, quantity, entry_price, state, protection_state,
             current, current, json.dumps(protective_order_ids) if protective_order_ids is not None else None),
        )
        self.connection.commit()

    @synchronized
    def managed_positions(self, active_only: bool = True) -> list[ManagedPosition]:
        if active_only:
            placeholders = ",".join("?" for _ in MANAGED_POSITION_STATES)
            rows = self.connection.execute(
                f"SELECT * FROM managed_positions WHERE state IN ({placeholders}) ORDER BY opened_at_ms",
                tuple(sorted(MANAGED_POSITION_STATES)),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM managed_positions ORDER BY opened_at_ms").fetchall()
        return [ManagedPosition(**dict(row)) for row in rows]

    @synchronized
    def mark_managed_partial_exit(
        self,
        plan_id: str,
        filled_quantity: float,
        fee_breakdown: dict[str, float],
    ) -> bool:
        cursor = self.connection.execute(
            """UPDATE managed_positions
                  SET state = ?, exit_filled_quantity = ?, exit_fee_breakdown_json = ?,
                      updated_at_ms = ?
                WHERE plan_id = ? AND state IN (?, ?, ?, ?)""",
            (
                OrderState.EXIT_PARTIALLY_FILLED.value,
                filled_quantity,
                json.dumps(fee_breakdown, sort_keys=True),
                now_ms(),
                plan_id,
                OrderState.PARTIALLY_FILLED.value,
                OrderState.FILLED.value,
                OrderState.POSITION_UNPROTECTED.value,
                OrderState.EXIT_PARTIALLY_FILLED.value,
            ),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def managed_open_count(self) -> int:
        return len(self.managed_positions(active_only=True))

    @synchronized
    def flatten_intent(self, plan_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """SELECT * FROM flatten_attempts WHERE plan_id = ?
               ORDER BY attempt_number DESC LIMIT 1""", (plan_id,),
        ).fetchone()
        return dict(row) if row else None

    @synchronized
    def flatten_attempt(self, plan_id: str, attempt_number: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM flatten_attempts WHERE plan_id = ? AND attempt_number = ?",
            (plan_id, attempt_number),
        ).fetchone()
        return dict(row) if row else None

    @synchronized
    def create_flatten_intent(
        self, plan_id: str, client_order_id: str, symbol: str, requested_quantity: float,
        attempt_number: int | None = None,
    ) -> dict[str, Any]:
        current = now_ms()
        attempt = attempt_number or int(self.connection.execute(
            "SELECT COALESCE(MAX(attempt_number), 0) + 1 value FROM flatten_attempts WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()["value"])
        self.connection.execute(
            """INSERT OR IGNORE INTO flatten_attempts
               (plan_id, attempt_number, client_order_id, symbol, requested_quantity, state,
                created_at_ms, updated_at_ms)
               VALUES (?, ?, ?, ?, ?, 'PREPARED', ?, ?)""",
            (plan_id, attempt, client_order_id, symbol, requested_quantity, current, current),
        )
        self.connection.commit()
        intent = self.flatten_attempt(plan_id, attempt)
        if intent is None:
            raise RuntimeError("FLATTEN_INTENT_PERSISTENCE_FAILED")
        return intent

    @synchronized
    def update_flatten_intent(
        self, plan_id: str, state: str, *, attempt_number: int | None = None, **fields: Any,
    ) -> dict[str, Any]:
        allowed = {
            "filled_quantity", "order_id", "raw_response_json", "last_error",
            "terminal_confirmed", "reconciliation_complete", "protection_cleanup_json",
        }
        invalid = set(fields).difference(allowed)
        if invalid:
            raise ValueError(f"INVALID_FLATTEN_INTENT_FIELDS:{sorted(invalid)}")
        values = dict(fields)
        values.update(state=state, updated_at_ms=now_ms())
        assignments = ", ".join(f"{key} = ?" for key in values)
        attempt = attempt_number or int(self.connection.execute(
            "SELECT MAX(attempt_number) value FROM flatten_attempts WHERE plan_id = ?", (plan_id,),
        ).fetchone()["value"] or 0)
        cursor = self.connection.execute(
            f"UPDATE flatten_attempts SET {assignments} WHERE plan_id = ? AND attempt_number = ?",
            (*values.values(), plan_id, attempt),
        )
        if cursor.rowcount != 1:
            self.connection.rollback()
            raise LookupError("FLATTEN_INTENT_NOT_FOUND")
        self.connection.commit()
        intent = self.flatten_attempt(plan_id, attempt)
        if intent is None:
            raise LookupError("FLATTEN_INTENT_NOT_FOUND")
        return intent

    @synchronized
    def flatten_intents(self, active_only: bool = False) -> list[dict[str, Any]]:
        if active_only:
            rows = self.connection.execute(
                "SELECT * FROM flatten_attempts "
                "WHERE state NOT IN ('FILLED', 'CANCELLED', 'REJECTED', 'FAILED') "
                "ORDER BY plan_id, attempt_number"
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM flatten_attempts ORDER BY plan_id, attempt_number"
            ).fetchall()
        return [dict(row) for row in rows]

    @synchronized
    def flatten_attempts_for_plan(self, plan_id: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM flatten_attempts WHERE plan_id = ? ORDER BY attempt_number",
            (plan_id,),
        ).fetchall()]

    @synchronized
    def upsert_reconciled_exit_fill(
        self,
        plan_id: str,
        fill_key: str,
        order_id: str | None,
        fill_timestamp_ms: int,
        quantity: float,
        price: float,
    ) -> None:
        self.connection.execute(
            """INSERT INTO reconciled_exit_fills
               (plan_id, fill_key, order_id, fill_timestamp_ms, quantity, price)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(plan_id, fill_key) DO UPDATE SET
                 order_id=excluded.order_id,
                 fill_timestamp_ms=excluded.fill_timestamp_ms,
                 quantity=excluded.quantity,
                 price=excluded.price""",
            (plan_id, fill_key, order_id, fill_timestamp_ms, quantity, price),
        )
        self.connection.commit()

    @synchronized
    def position_slots_in_use(self, exclude_plan_id: str | None = None) -> int:
        """Count managed inventory and reserved entry orders once per plan."""
        reserved_states = tuple(sorted(ACTIVE_ORDER_STATES))
        managed_states = tuple(sorted(MANAGED_POSITION_STATES))
        excluded = exclude_plan_id or ""
        order_marks = ",".join("?" for _ in reserved_states)
        position_marks = ",".join("?" for _ in managed_states)
        row = self.connection.execute(
            f"""SELECT COUNT(*) count FROM (
                  SELECT plan_id FROM managed_positions
                   WHERE state IN ({position_marks}) AND plan_id != ?
                  UNION
                  SELECT plan_id FROM order_lifecycle
                   WHERE state IN ({order_marks}) AND plan_id != ?
                )""",
            (*managed_states, excluded, *reserved_states, excluded),
        ).fetchone()
        return int(row["count"])

    @synchronized
    def reserved_entry_notional(self, exclude_plan_id: str | None = None) -> float:
        reserved_states = (
            OrderState.APPROVED.value, OrderState.SUBMITTED.value,
            OrderState.SUBMISSION_UNKNOWN.value, OrderState.CANCEL_REQUESTED.value,
            OrderState.OPEN.value,
            OrderState.PARTIALLY_FILLED.value,
        )
        marks = ",".join("?" for _ in reserved_states)
        row = self.connection.execute(
            f"""SELECT COALESCE(SUM(
                    MAX(requested_size - filled_size, 0) * expected_execution_price
                  ), 0) reserved
                  FROM order_lifecycle
                 WHERE state IN ({marks}) AND plan_id != ?
                   AND expected_execution_price IS NOT NULL""",
            (*reserved_states, exclude_plan_id or ""),
        ).fetchone()
        return float(row["reserved"])

    @synchronized
    def record_submission(self, plan_id: str, symbol: str, side: str, strategy: str, response: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO order_submissions VALUES (?, ?, ?, ?, ?, ?)",
            (plan_id, now_ms(), symbol, side, strategy, json.dumps(response, default=str)),
        )
        self.connection.commit()

    @synchronized
    def daily_state(self, open_position_count: int | None = None) -> DailyRiskState:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        start_ms = int(datetime.fromisoformat(today).replace(tzinfo=UTC).timestamp() * 1000)
        rows = self.connection.execute(
            "SELECT COALESCE(exit_time_ms, timestamp_ms) timestamp_ms, COALESCE(net_pnl, pnl) pnl "
            "FROM trades WHERE COALESCE(exit_time_ms, timestamp_ms) >= ? "
            "AND COALESCE(net_pnl, pnl) IS NOT NULL ORDER BY timestamp_ms", (start_ms,),
        ).fetchall()
        consecutive = 0
        for row in reversed(rows):
            if float(row["pnl"]) < 0:
                consecutive += 1
            else:
                break
        return DailyRiskState(
            realized_pnl=sum(float(row["pnl"]) for row in rows), consecutive_losses=consecutive,
            last_trade_timestamp_ms=int(rows[-1]["timestamp_ms"]) if rows else None,
            open_position_count=self.position_slots_in_use() if open_position_count is None else open_position_count,
        )

    @synchronized
    def get_orders(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM order_lifecycle ORDER BY created_at_ms DESC"
        ).fetchall()]

    @synchronized
    def get_trades(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM trades ORDER BY COALESCE(exit_time_ms, timestamp_ms) DESC"
        ).fetchall()]

    @synchronized
    def reconciliation_cursor(self, symbol: str, stream_kind: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM reconciliation_cursors WHERE symbol = ? AND stream_kind = ?",
            (symbol, stream_kind),
        ).fetchone()
        return dict(row) if row is not None else None

    @synchronized
    def update_reconciliation_cursor(
        self,
        symbol: str,
        stream_kind: str,
        last_timestamp_ms: int | None,
        last_fill_id: str | None,
    ) -> None:
        self.connection.execute(
            """INSERT INTO reconciliation_cursors
               (symbol, stream_kind, last_timestamp_ms, last_fill_id, updated_at_ms)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(symbol, stream_kind) DO UPDATE SET
                 last_timestamp_ms=excluded.last_timestamp_ms,
                 last_fill_id=excluded.last_fill_id,
                 updated_at_ms=excluded.updated_at_ms""",
            (symbol, stream_kind, last_timestamp_ms, last_fill_id, now_ms()),
        )
        self.connection.commit()

    @synchronized
    def record_reconciliation_event(
        self,
        plan_id: str | None,
        symbol: str,
        result_code: str,
        details: dict[str, Any],
    ) -> None:
        self.connection.execute(
            """INSERT INTO reconciliation_audit
               (timestamp_ms, plan_id, symbol, result_code, details_json)
               VALUES (?, ?, ?, ?, ?)""",
            (now_ms(), plan_id, symbol, result_code, json.dumps(details, default=str, sort_keys=True)),
        )
        self.connection.commit()

    @synchronized
    def reconciliation_events(self, limit: int = 200) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 1000))
        rows = self.connection.execute(
            "SELECT * FROM reconciliation_audit ORDER BY timestamp_ms DESC, id DESC LIMIT ?",
            (safe_limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    @synchronized
    def record_reconciled_exit_fills(self, plan_id: str, fills: list[dict[str, Any]]) -> int:
        """Persist normalized fill facts only; exchange raw payloads are never authoritative state."""
        inserted = 0
        for fill in fills:
            cursor = self.connection.execute(
                """INSERT OR IGNORE INTO reconciled_exit_fills
                   (plan_id, fill_key, order_id, fill_timestamp_ms, quantity, price,
                    fee, fee_currency)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    plan_id,
                    str(fill["fill_key"]),
                    fill.get("order_id"),
                    int(fill["fill_timestamp_ms"]),
                    float(fill["quantity"]),
                    float(fill["price"]),
                    float(fill["fee"]) if fill.get("fee") is not None else None,
                    fill.get("fee_currency"),
                ),
            )
            inserted += cursor.rowcount
        self.connection.commit()
        return inserted

    @synchronized
    def reconciled_exit_fills(self, plan_id: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            """SELECT * FROM reconciled_exit_fills WHERE plan_id = ?
               ORDER BY fill_timestamp_ms, fill_key""",
            (plan_id,),
        ).fetchall()]

    @synchronized
    def record_entry_fill(
        self, plan: TradePlan, order_id: str | None, entry_time_ms: int,
        entry_price: float, quantity: float, fees: float | None,
        slippage_abs: float | None, slippage_pct: float | None,
    ) -> None:
        if plan.stop is None or plan.take_profit is None:
            raise ValueError("TRADE_PROTECTION_PRICES_MISSING")
        self.connection.execute(
            """INSERT OR IGNORE INTO trades
               (timestamp_ms, environment, backend, symbol, side, entry, exit, size, stop,
                take_profit, fees, slippage, pnl, holding_time_seconds, strategy, signal_score,
                plan_id, order_id, entry_time_ms, entry_price, quantity, slippage_abs,
                slippage_pct, fee_status)
               VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (entry_time_ms, plan.environment, plan.backend, plan.symbol, plan.side, entry_price,
             quantity, plan.stop, plan.take_profit, fees, slippage_abs, plan.strategy,
             plan.signal_score, plan.plan_id, order_id, entry_time_ms, entry_price, quantity,
             slippage_abs, slippage_pct, "ACTUAL" if fees is not None else "UNKNOWN"),
        )
        self.connection.execute(
            """UPDATE trades
                  SET entry = ?, size = ?, entry_price = ?, quantity = ?,
                      order_id = COALESCE(?, order_id), slippage_abs = ?, slippage_pct = ?
                WHERE plan_id = ? AND exit_time_ms IS NULL
                  AND COALESCE(quantity, 0) < ?""",
            (
                entry_price, quantity, entry_price, quantity, order_id,
                slippage_abs, slippage_pct, plan.plan_id, quantity,
            ),
        )
        if fees is not None:
            self.connection.execute(
                """UPDATE trades SET fees = ?, fee_status = 'ACTUAL'
                   WHERE plan_id = ? AND exit_time_ms IS NULL AND fees IS NULL""",
                (fees, plan.plan_id),
            )
        self.connection.commit()

    @synchronized
    def close_trade(
        self, plan_id: str, exit_time_ms: int, exit_price: float,
        exit_fees: float | None, exit_order_id: str | None = None,
        *,
        exit_filled_quantity: float | None = None,
        exit_fee_breakdown: dict[str, float] | None = None,
        exit_fee_conversion_required: bool = False,
    ) -> None:
        with self._lock:
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                row = self.connection.execute(
                    "SELECT entry_price, quantity, fees, entry_time_ms FROM trades WHERE plan_id = ?",
                    (plan_id,),
                ).fetchone()
                if row is None:
                    raise LookupError("TRADE_NOT_FOUND")
                gross = (exit_price - float(row["entry_price"])) * float(row["quantity"])
                entry_fees = float(row["fees"]) if row["fees"] is not None else None
                total_fees = entry_fees + exit_fees if entry_fees is not None and exit_fees is not None else None
                net = gross - total_fees if total_fees is not None else None
                holding = (exit_time_ms - int(row["entry_time_ms"])) / 1000
                self.connection.execute(
                    """UPDATE trades SET exit = ?, exit_price = ?, exit_time_ms = ?, gross_pnl = ?,
                       fees = ?, pnl = ?, net_pnl = ?, holding_time_seconds = ?,
                       fee_status = ?, exit_order_id = ? WHERE plan_id = ? AND exit_time_ms IS NULL""",
                    (exit_price, exit_price, exit_time_ms, gross, total_fees, net, net, holding,
                     (
                         "ACTUAL" if total_fees is not None
                         else "CURRENCY_CONVERSION_REQUIRED" if exit_fee_conversion_required
                         else "UNKNOWN"
                     ),
                     exit_order_id,
                     plan_id),
                )
                position = self.connection.execute(
                    "UPDATE managed_positions SET state = ?, closed_at_ms = ?, updated_at_ms = ?, "
                    "exit_order_id = ?, exit_filled_quantity = COALESCE(?, exit_filled_quantity), "
                    "exit_fee_breakdown_json = COALESCE(?, exit_fee_breakdown_json) "
                    "WHERE plan_id = ? AND state IN (?, ?, ?, ?)",
                    (OrderState.CLOSED.value, exit_time_ms, now_ms(), exit_order_id,
                     exit_filled_quantity,
                     json.dumps(exit_fee_breakdown, sort_keys=True)
                     if exit_fee_breakdown is not None else None,
                     plan_id,
                     OrderState.PARTIALLY_FILLED.value, OrderState.FILLED.value,
                     OrderState.POSITION_UNPROTECTED.value, OrderState.EXIT_PARTIALLY_FILLED.value),
                )
                if position.rowcount != 1:
                    raise StateChangedError("MANAGED_POSITION_NOT_ACTIVE")
                lifecycle = self.connection.execute(
                    "UPDATE order_lifecycle SET state = ?, closed_at_ms = ?, updated_at_ms = ? "
                    "WHERE plan_id = ? AND state IN (?, ?, ?, ?)",
                    (OrderState.CLOSED.value, exit_time_ms, now_ms(), plan_id,
                     OrderState.PARTIALLY_FILLED.value, OrderState.FILLED.value,
                     OrderState.POSITION_UNPROTECTED.value, OrderState.CANCELLED.value),
                )
                if lifecycle.rowcount != 1:
                    raise StateChangedError("ENTRY_LIFECYCLE_NOT_CLOSABLE")
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise

    @synchronized
    def signal_calibration(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT signal_score score_bucket, COUNT(*) sample_size,
                      SUM(CASE WHEN COALESCE(net_pnl, pnl) > 0 THEN 1 ELSE 0 END) wins,
                      AVG(COALESCE(net_pnl, pnl)) expectancy
               FROM trades WHERE COALESCE(net_pnl, pnl) IS NOT NULL
               GROUP BY signal_score ORDER BY signal_score"""
        ).fetchall()
        return [dict(row) | {"empirical_win_rate": row["wins"] / row["sample_size"]} for row in rows]

    @synchronized
    def close(self) -> None:
        self.connection.close()
