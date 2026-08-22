from __future__ import annotations

import json
from typing import Any

from decision.trade_plan import TradePlan
from execution.demo_executor import DemoExecutor
from execution.order_state import OrderState
from storage.trade_store import now_ms


EXPLICIT_APPROVALS = {"确认执行", "CONFIRM EXECUTION", "CONFIRM DEMO ORDER"}


def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    value: Any = payload.get("data", payload)
    if isinstance(value, dict) and isinstance(value.get("data"), list):
        value = value["data"]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict) and value:
        return [value]
    return []


def _map_state(row: dict[str, Any], requested_size: float) -> tuple[str, float, float | None]:
    raw = str(row.get("state") or row.get("status") or "").lower()
    filled = float(row.get("accFillSz") or row.get("fillSz") or row.get("filledSz") or 0)
    average_text = row.get("avgPx") or row.get("fillPx")
    average = float(average_text) if average_text not in (None, "") else None
    if raw in {"canceled", "cancelled"}:
        return OrderState.CANCELLED.value, filled, average
    if raw in {"rejected", "failed"}:
        return OrderState.REJECTED.value, filled, average
    if raw in {"filled", "completed"} or (requested_size > 0 and filled >= requested_size):
        return OrderState.FILLED.value, filled, average
    if raw in {"partially_filled", "partially-filled"} or 0 < filled < requested_size:
        return OrderState.PARTIALLY_FILLED.value, filled, average
    if raw in {"live", "open", "pending", "effective"}:
        return OrderState.OPEN.value, filled, average
    return OrderState.SUBMITTED.value, filled, average


class OrderManager:
    def __init__(self, executor: DemoExecutor, store: Any) -> None:
        self.executor = executor
        self.store = store

    @staticmethod
    def client_order_id(plan_id: str) -> str:
        return plan_id[:32]

    def submit(self, plan: TradePlan, approval_text: str, expected_price: float | None = None) -> dict[str, Any]:
        if approval_text.strip() not in EXPLICIT_APPROVALS:
            raise PermissionError("EXPLICIT_USER_APPROVAL_REQUIRED")
        if not plan.risk_approved:
            raise PermissionError("RISK_MANAGER_REJECTED")
        if self.store.is_duplicate(plan.plan_id):
            raise RuntimeError("DUPLICATE_ORDER")
        client_id = self.client_order_id(plan.plan_id)
        if not self.store.approve_and_create_order(plan, client_id, expected_price or plan.entry):
            raise RuntimeError("PLAN_NOT_PENDING_OR_DUPLICATE_APPROVAL")
        self.store.transition_order(
            plan.plan_id, OrderState.SUBMITTED.value, submitted_at_ms=now_ms(),
            protection_state="ATTACHED_REQUESTED",
        )
        try:
            result = self.executor.execute(plan)
        except Exception as exc:
            self.store.transition_order(
                plan.plan_id, OrderState.SUBMISSION_UNKNOWN.value, last_error=type(exc).__name__
            )
            reconciled = self.reconcile_plan(plan.plan_id)
            if reconciled.get("found"):
                return reconciled
            raise RuntimeError("SUBMISSION_UNKNOWN_RECONCILIATION_REQUIRED") from exc
        row = _rows(result)[0] if _rows(result) else {}
        if str(row.get("sCode", "0")) not in {"", "0"}:
            self.store.transition_order(
                plan.plan_id, OrderState.REJECTED.value,
                raw_response_json=json.dumps(result, default=str), last_error="OKX_ORDER_REJECTED",
            )
            raise RuntimeError("OKX_ORDER_REJECTED")
        order_id = str(row.get("ordId") or row.get("orderId") or "") or None
        self.store.transition_order(
            plan.plan_id, OrderState.SUBMITTED.value, okx_order_id=order_id,
            raw_response_json=json.dumps(result, default=str),
        )
        self.store.record_submission(plan.plan_id, plan.symbol, plan.side, plan.strategy, result)
        return {"plan_id": plan.plan_id, "client_order_id": client_id,
                "okx_order_id": order_id, "state": OrderState.SUBMITTED.value, "response": result}

    def _apply_remote_order(self, local: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
        state, filled, average = _map_state(row, float(local["requested_size"]))
        expected = local.get("expected_execution_price")
        slippage_abs = None
        slippage_pct = None
        if average is not None and expected:
            slippage_abs = average - float(expected)
            slippage_pct = abs(slippage_abs) / float(expected) * 100
        order_id = str(row.get("ordId") or local.get("okx_order_id") or "") or None
        fields: dict[str, Any] = {
            "okx_order_id": order_id, "filled_size": filled,
            "average_fill_price": average, "slippage_abs": slippage_abs,
            "slippage_pct": slippage_pct, "raw_response_json": json.dumps(row, default=str),
        }
        if state == OrderState.FILLED.value:
            fields["filled_at_ms"] = now_ms()
        self.store.transition_order(local["plan_id"], state, **fields)
        if state in {OrderState.PARTIALLY_FILLED.value, OrderState.FILLED.value}:
            protection = self._protection_state(local, order_id)
            final_state = state
            if state == OrderState.FILLED.value and protection != "PROTECTED":
                final_state = OrderState.POSITION_UNPROTECTED.value
                self.store.transition_order(
                    local["plan_id"], final_state, protection_state=protection,
                    last_error="STOP_LOSS_NOT_VERIFIED",
                )
            else:
                self.store.transition_order(local["plan_id"], state, protection_state=protection)
            self.store.upsert_managed_position(
                local["plan_id"], order_id, local["symbol"], filled, average,
                final_state, protection,
            )
            state = final_state
        self._backfill_fill_data(local, order_id)
        if state in {OrderState.FILLED.value, OrderState.POSITION_UNPROTECTED.value} and average is not None:
            plan = self.store.get_plan(local["plan_id"])
            updated = self.store.order_for_plan(local["plan_id"])
            if plan is not None and updated is not None:
                self.store.record_entry_fill(
                    plan, order_id, int(updated.get("filled_at_ms") or now_ms()), average, filled,
                    updated.get("fee"), slippage_abs, slippage_pct,
                )
        return {"found": True, "plan_id": local["plan_id"], "state": state,
                "filled_size": filled, "average_fill_price": average, "okx_order_id": order_id}

    def _protection_state(self, local: dict[str, Any], order_id: str | None) -> str:
        try:
            rows = _rows(self.executor.backend.get_protection_orders(local["symbol"]))
        except (NotImplementedError, RuntimeError):
            return "TP_SL_BACKEND_NOT_SUPPORTED"
        client_id = local["client_order_id"]
        protected = any(
            str(row.get("ordId") or row.get("attachAlgoId") or "") == str(order_id or "")
            or str(row.get("clOrdId") or row.get("algoClOrdId") or "") == client_id
            for row in rows
        )
        return "PROTECTED" if protected else "PROTECTION_NOT_FOUND"

    def _backfill_fill_data(self, local: dict[str, Any], order_id: str | None) -> None:
        if not order_id:
            return
        try:
            fills = [row for row in _rows(self.executor.backend.get_fills(local["symbol"]))
                     if str(row.get("ordId", "")) == order_id]
        except Exception:
            return
        if not fills:
            return
        fees = [float(row["fee"]) for row in fills if row.get("fee") not in (None, "")]
        fee = sum(fees) if len(fees) == len(fills) else None
        fee_currency = next((str(row["feeCcy"]) for row in fills if row.get("feeCcy")), None)
        self.store.transition_order(local["plan_id"], self.store.order_for_plan(local["plan_id"])["state"],
                                    fee=fee, fee_currency=fee_currency)

    def reconcile_plan(self, plan_id: str) -> dict[str, Any]:
        local = self.store.order_for_plan(plan_id)
        if local is None:
            return {"found": False, "reason": "LOCAL_ORDER_NOT_FOUND"}
        payload: dict[str, Any] | None = None
        try:
            payload = self.executor.backend.get_order_by_client_id(local["symbol"], local["client_order_id"])
        except Exception:
            if local.get("okx_order_id"):
                try:
                    payload = self.executor.backend.get_order(local["symbol"], local["okx_order_id"])
                except Exception:
                    payload = None
        rows = _rows(payload or {})
        if not rows:
            return {"found": False, "plan_id": plan_id, "state": local["state"]}
        return self._apply_remote_order(local, rows[0])

    def recover_active_orders(self) -> list[dict[str, Any]]:
        return [self.reconcile_plan(order["plan_id"]) for order in self.store.active_orders()]

    def query(self, symbol: str, order_id: str) -> dict[str, Any]:
        return self.executor.backend.get_order(symbol, order_id)

    def cancel(self, plan_id: str, approval_text: str) -> dict[str, Any]:
        if approval_text.strip() not in EXPLICIT_APPROVALS:
            raise PermissionError("EXPLICIT_USER_APPROVAL_REQUIRED")
        local = self.store.order_for_plan(plan_id)
        if not local or not local.get("okx_order_id"):
            raise LookupError("ORDER_NOT_CANCELLABLE")
        result = self.executor.backend.cancel_order(local["symbol"], local["okx_order_id"])
        row = _rows(result)[0] if _rows(result) else {}
        if str(row.get("sCode", "0")) not in {"", "0"}:
            raise RuntimeError("CANCEL_REJECTED")
        self.store.transition_order(plan_id, OrderState.CANCELLED.value, closed_at_ms=now_ms(),
                                    raw_response_json=json.dumps(result, default=str))
        return result
