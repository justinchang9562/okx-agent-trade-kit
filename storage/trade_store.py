from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from decision.trade_plan import TradePlan
from execution.order_state import ACTIVE_ORDER_STATES, ALLOWED_TRANSITIONS, MANAGED_POSITION_STATES, OrderState
from risk.daily_limits import DailyRiskState
from storage.database import connect


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


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


class TradeStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection = connect(path)

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

    def update_pending_plan_snapshot(self, plan: TradePlan) -> bool:
        cursor = self.connection.execute(
            "UPDATE trade_plans SET plan_json = ?, updated_at_ms = ? WHERE plan_id = ? AND status = ?",
            (json.dumps(plan.as_dict(), default=str), now_ms(), plan.plan_id, OrderState.PLANNED.value),
        )
        self.connection.commit()
        return cursor.rowcount == 1

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

    def is_duplicate(self, plan_id: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM order_lifecycle WHERE plan_id = ? UNION SELECT 1 FROM order_submissions WHERE plan_id = ?",
            (plan_id, plan_id),
        ).fetchone()
        return row is not None

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

    def approve_and_create_order(self, plan: TradePlan, client_order_id: str, expected_price: float) -> bool:
        """Atomic approval/idempotency boundary; safe across concurrent duplicate approvals."""
        current = now_ms()
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

    def transition_order(self, plan_id: str, state: str, **fields: Any) -> None:
        allowed = {
            "okx_order_id", "filled_size", "average_fill_price", "expected_execution_price",
            "slippage_abs", "slippage_pct", "fee", "fee_currency", "protection_state",
            "submitted_at_ms", "filled_at_ms", "closed_at_ms", "raw_response_json", "last_error",
        }
        invalid = set(fields).difference(allowed)
        if invalid:
            raise ValueError(f"INVALID_LIFECYCLE_FIELDS:{sorted(invalid)}")
        current_row = self.connection.execute(
            "SELECT state FROM order_lifecycle WHERE plan_id = ?", (plan_id,)
        ).fetchone()
        if current_row is None:
            raise LookupError("ORDER_LIFECYCLE_NOT_FOUND")
        current_state = str(current_row["state"])
        if state not in ALLOWED_TRANSITIONS.get(current_state, set()):
            raise RuntimeError(f"INVALID_ORDER_TRANSITION:{current_state}->{state}")
        values = dict(fields)
        values.update(state=state, updated_at_ms=now_ms())
        assignments = ", ".join(f"{key} = ?" for key in values)
        cursor = self.connection.execute(
            f"UPDATE order_lifecycle SET {assignments} WHERE plan_id = ?", (*values.values(), plan_id)
        )
        self.connection.execute(
            "UPDATE trade_plans SET status = ?, updated_at_ms = ? WHERE plan_id = ?",
            (state, now_ms(), plan_id),
        )
        self.connection.commit()

    def order_for_plan(self, plan_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM order_lifecycle WHERE plan_id = ?", (plan_id,)).fetchone()
        return dict(row) if row else None

    def active_orders(self) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in ACTIVE_ORDER_STATES)
        rows = self.connection.execute(
            f"SELECT * FROM order_lifecycle WHERE state IN ({placeholders}) ORDER BY created_at_ms",
            tuple(sorted(ACTIVE_ORDER_STATES)),
        ).fetchall()
        return [dict(row) for row in rows]

    def upsert_managed_position(
        self, plan_id: str, order_id: str | None, symbol: str, quantity: float,
        entry_price: float | None, state: str, protection_state: str,
    ) -> None:
        current = now_ms()
        self.connection.execute(
            """INSERT INTO managed_positions
               (plan_id, order_id, symbol, quantity, entry_price, state, protection_state,
                opened_at_ms, updated_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(plan_id) DO UPDATE SET order_id=excluded.order_id,
                 quantity=excluded.quantity, entry_price=excluded.entry_price, state=excluded.state,
                 protection_state=excluded.protection_state, updated_at_ms=excluded.updated_at_ms""",
            (plan_id, order_id, symbol, quantity, entry_price, state, protection_state, current, current),
        )
        self.connection.commit()

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

    def managed_open_count(self) -> int:
        return len(self.managed_positions(active_only=True))

    def record_submission(self, plan_id: str, symbol: str, side: str, strategy: str, response: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO order_submissions VALUES (?, ?, ?, ?, ?, ?)",
            (plan_id, now_ms(), symbol, side, strategy, json.dumps(response, default=str)),
        )
        self.connection.commit()

    def daily_state(self, open_position_count: int | None = None) -> DailyRiskState:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        start_ms = int(datetime.fromisoformat(today).replace(tzinfo=timezone.utc).timestamp() * 1000)
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
            open_position_count=self.managed_open_count() if open_position_count is None else open_position_count,
        )

    def get_orders(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM order_lifecycle ORDER BY created_at_ms DESC"
        ).fetchall()]

    def get_trades(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM trades ORDER BY COALESCE(exit_time_ms, timestamp_ms) DESC"
        ).fetchall()]

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
        self.connection.commit()

    def close_trade(
        self, plan_id: str, exit_time_ms: int, exit_price: float,
        exit_fees: float | None,
    ) -> None:
        row = self.connection.execute(
            "SELECT entry_price, quantity, fees, entry_time_ms FROM trades WHERE plan_id = ?", (plan_id,)
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
               fee_status = ? WHERE plan_id = ?""",
            (exit_price, exit_price, exit_time_ms, gross, total_fees, net, net, holding,
             "ACTUAL" if total_fees is not None else "UNKNOWN", plan_id),
        )
        self.connection.commit()

    def signal_calibration(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT signal_score score_bucket, COUNT(*) sample_size,
                      SUM(CASE WHEN COALESCE(net_pnl, pnl) > 0 THEN 1 ELSE 0 END) wins,
                      AVG(COALESCE(net_pnl, pnl)) expectancy
               FROM trades WHERE COALESCE(net_pnl, pnl) IS NOT NULL
               GROUP BY signal_score ORDER BY signal_score"""
        ).fetchall()
        return [dict(row) | {"empirical_win_rate": row["wins"] / row["sample_size"]} for row in rows]

    def close(self) -> None:
        self.connection.close()
