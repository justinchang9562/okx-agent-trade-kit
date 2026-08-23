from __future__ import annotations

import hashlib
import json
from typing import Any

from decision.trade_plan import TradePlan
from execution.demo_executor import DemoExecutor
from execution.errors import PreSubmitRejectedError, SubmissionUncertainError
from execution.order_state import OrderState
from execution.targeted_reconciler import TargetedOrderReconciler
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
    MAX_FILL_PAGES = 20
    RECENT_FILL_WINDOW_MS = 3 * 24 * 60 * 60 * 1000
    ARCHIVE_FILL_WINDOW_MS = 90 * 24 * 60 * 60 * 1000

    def __init__(
        self,
        executor: DemoExecutor,
        store: Any,
        targeted_reconciler: TargetedOrderReconciler | None = None,
    ) -> None:
        self.executor = executor
        self.store = store
        self.targeted_reconciler = targeted_reconciler or TargetedOrderReconciler()

    @staticmethod
    def client_order_id(plan_id: str) -> str:
        return plan_id[:32]

    def submit(self, plan: TradePlan, approval_text: str, expected_price: float | None = None) -> dict[str, Any]:
        if approval_text.strip() not in EXPLICIT_APPROVALS:
            raise PermissionError("EXPLICIT_USER_APPROVAL_REQUIRED")
        return self._submit(plan, expected_price)

    def submit_automatic(self, plan: TradePlan, expected_price: float | None = None) -> dict[str, Any]:
        """Submit after server-side session authorization and the final deterministic risk veto."""
        return self._submit(plan, expected_price)

    def _submit(self, plan: TradePlan, expected_price: float | None = None) -> dict[str, Any]:
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
        except PreSubmitRejectedError as exc:
            self.store.transition_order(
                plan.plan_id, OrderState.REJECTED.value,
                last_error=f"PRE_SUBMIT_REJECTED:{str(exc)}",
            )
            raise RuntimeError("PRE_SUBMIT_REJECTED") from exc
        except (SubmissionUncertainError, TimeoutError, ConnectionError) as exc:
            self.store.transition_order(
                plan.plan_id, OrderState.SUBMISSION_UNKNOWN.value, last_error=type(exc).__name__
            )
            reconciled = self._new_order_fast_lane(plan.plan_id)
            if reconciled.get("found"):
                return reconciled
            raise RuntimeError("SUBMISSION_UNKNOWN_RECONCILIATION_REQUIRED") from exc
        except Exception as exc:
            self.store.transition_order(
                plan.plan_id, OrderState.REJECTED.value,
                last_error=f"PRE_SUBMIT_REJECTED:{type(exc).__name__}",
            )
            raise RuntimeError("PRE_SUBMIT_REJECTED") from exc
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
        reconciled = self._new_order_fast_lane(plan.plan_id)
        if reconciled.get("found"):
            return reconciled | {"response": result}
        return reconciled | {
            "plan_id": plan.plan_id,
            "client_order_id": client_id,
            "okx_order_id": order_id,
            "state": OrderState.SUBMITTED.value,
            "reconciliation": "RECONCILIATION_REQUIRED",
            "response": result,
        }

    def _new_order_fast_lane(self, plan_id: str) -> dict[str, Any]:
        result = self.targeted_reconciler.run(
            lambda: self.reconcile_plan(plan_id),
            lambda item: item.get("state") in {
                OrderState.FILLED.value,
                OrderState.CANCELLED.value,
                OrderState.REJECTED.value,
            },
        )
        if result.get("state") == OrderState.POSITION_UNPROTECTED.value:
            return result | {"reconciliation": "POSITION_UNPROTECTED"}
        if not result.get("found") or result.get("state") in {
            OrderState.SUBMITTED.value,
            OrderState.SUBMISSION_UNKNOWN.value,
            OrderState.OPEN.value,
            OrderState.PARTIALLY_FILLED.value,
        }:
            return result | {"reconciliation": "RECONCILIATION_REQUIRED"}
        return result | {"reconciliation": "COMPLETE"}

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
        if state in {OrderState.CANCELLED.value, OrderState.REJECTED.value}:
            fields["closed_at_ms"] = now_ms()
        self.store.transition_order(local["plan_id"], state, **fields)
        lifecycle_state = state
        managed_state = state
        has_managed_fill = filled > 0 and state in {
            OrderState.PARTIALLY_FILLED.value,
            OrderState.FILLED.value,
            OrderState.CANCELLED.value,
        }
        if has_managed_fill:
            protection, protective_order_ids = self._protection_state(local, order_id, filled)
            managed_state = (
                OrderState.PARTIALLY_FILLED.value
                if state == OrderState.CANCELLED.value else state
            )
            if protection != "PROTECTED" and state in {
                OrderState.FILLED.value,
                OrderState.CANCELLED.value,
            }:
                managed_state = OrderState.POSITION_UNPROTECTED.value
            if state == OrderState.FILLED.value and managed_state == OrderState.POSITION_UNPROTECTED.value:
                lifecycle_state = OrderState.POSITION_UNPROTECTED.value
                self.store.transition_order(
                    local["plan_id"], lifecycle_state, protection_state=protection,
                    last_error="PROTECTION_NOT_VERIFIED",
                )
            else:
                self.store.transition_order(
                    local["plan_id"], lifecycle_state, protection_state=protection,
                    last_error=("PROTECTION_NOT_VERIFIED" if protection != "PROTECTED" else None),
                )
            self.store.upsert_managed_position(
                local["plan_id"], order_id, local["symbol"], filled, average,
                managed_state, protection, protective_order_ids,
            )
            state = lifecycle_state
        self._backfill_fill_data(local, order_id)
        if has_managed_fill and average is not None:
            plan = self.store.get_plan(local["plan_id"])
            updated = self.store.order_for_plan(local["plan_id"])
            if plan is not None and updated is not None:
                self.store.record_entry_fill(
                    plan, order_id, int(updated.get("filled_at_ms") or now_ms()), average, filled,
                    updated.get("fee"), slippage_abs, slippage_pct,
                )
        return {
            "found": True,
            "plan_id": local["plan_id"],
            "state": state,
            "filled_size": filled,
            "average_fill_price": average,
            "okx_order_id": order_id,
            "managed_position_state": managed_state if has_managed_fill else None,
        }

    def _protection_state(
        self, local: dict[str, Any], order_id: str | None, filled_quantity: float,
    ) -> tuple[str, list[str]]:
        try:
            rows = _rows(self.executor.backend.get_protection_orders(local["symbol"]))
        except (NotImplementedError, RuntimeError):
            return "TP_SL_BACKEND_NOT_SUPPORTED", []
        plan = self.store.get_plan(local["plan_id"])
        if plan is None:
            return "PROTECTION_PLAN_NOT_FOUND", []
        client_id = str(local["client_order_id"] or "")
        matching = [row for row in rows if (
            bool(order_id)
            and str(row.get("ordId") or row.get("attachAlgoId") or "") == str(order_id)
            or bool(client_id)
            and str(row.get("clOrdId") or row.get("algoClOrdId") or "") == client_id
        )]
        identifiers = sorted({
            str(row.get("algoId") or row.get("attachAlgoId") or row.get("ordId") or "")
            for row in matching
            if row.get("algoId") or row.get("attachAlgoId") or row.get("ordId")
        })
        if not matching:
            return "PROTECTION_NOT_FOUND", identifiers
        try:
            instrument_rows = _rows(self.executor.backend.get_instrument(local["symbol"]))
            tick_size = float(instrument_rows[0]["tickSz"])
        except Exception:
            return "PROTECTION_INSTRUMENT_UNAVAILABLE", identifiers

        active_states = {"live", "open", "effective", "partially_effective"}
        sl_valid = False
        tp_valid = False
        quantity_invalid = False
        price_invalid = False
        inactive = False
        tolerance = max(tick_size / 2, 1e-12)
        for row in matching:
            raw_state = str(row.get("state") or row.get("status") or "").lower()
            if raw_state not in active_states:
                inactive = True
                continue
            quantity_text = row.get("sz") or row.get("ordSz") or row.get("actualSz")
            try:
                covered = float(quantity_text)
            except (TypeError, ValueError):
                quantity_invalid = True
                continue
            if covered + 1e-12 < filled_quantity:
                quantity_invalid = True
                continue
            sl_text = row.get("slTriggerPx")
            tp_text = row.get("tpTriggerPx")
            if sl_text not in (None, "", "0"):
                try:
                    sl_valid = sl_valid or abs(float(sl_text) - float(plan.stop)) <= tolerance
                    price_invalid = price_invalid or abs(float(sl_text) - float(plan.stop)) > tolerance
                except (TypeError, ValueError):
                    price_invalid = True
            if tp_text not in (None, "", "0"):
                try:
                    tp_valid = tp_valid or abs(float(tp_text) - float(plan.take_profit)) <= tolerance
                    price_invalid = price_invalid or abs(float(tp_text) - float(plan.take_profit)) > tolerance
                except (TypeError, ValueError):
                    price_invalid = True
        if sl_valid and tp_valid:
            return "PROTECTED", identifiers
        if quantity_invalid:
            return "PROTECTION_QUANTITY_MISMATCH", identifiers
        if price_invalid:
            return "PROTECTION_PRICE_MISMATCH", identifiers
        if inactive:
            return "PROTECTION_NOT_ACTIVE", identifiers
        if not sl_valid and not tp_valid:
            return "STOP_LOSS_AND_TAKE_PROFIT_NOT_VERIFIED", identifiers
        return ("STOP_LOSS_NOT_VERIFIED" if not sl_valid else "TAKE_PROFIT_NOT_VERIFIED"), identifiers

    def _backfill_fill_data(self, local: dict[str, Any], order_id: str | None) -> None:
        if not order_id:
            return
        try:
            capabilities = self.executor.backend.capabilities()
            current = now_ms()
            age = max(0, current - int(local.get("created_at_ms") or current))
            archive = age > self.RECENT_FILL_WINDOW_MS
            if archive and (age > self.ARCHIVE_FILL_WINDOW_MS or not capabilities.get("fills_archive")):
                return
            fills, complete = self._fill_pages(
                local["symbol"],
                capabilities,
                order_id=order_id if capabilities.get("fill_order_lookup") else None,
                begin_ms=int(local.get("created_at_ms") or current),
                end_ms=current,
                archive=archive,
            )
        except Exception:
            return
        fills = [row for row in fills if str(row.get("ordId", "")) == order_id]
        if not complete or not fills:
            return
        currencies = {str(row["feeCcy"]).upper() for row in fills if row.get("feeCcy")}
        fees = [abs(float(row["fee"])) for row in fills if row.get("fee") not in (None, "")]
        quote_currency = str(local["symbol"]).split("-")[-1].upper()
        fee = sum(fees) if len(fees) == len(fills) and currencies == {quote_currency} else None
        fee_currency = next(iter(currencies)) if len(currencies) == 1 else None
        self.store.transition_order(local["plan_id"], self.store.order_for_plan(local["plan_id"])["state"],
                                    fee=fee, fee_currency=fee_currency)

    def reconcile_plan(self, plan_id: str) -> dict[str, Any]:
        local = self.store.order_for_plan(plan_id)
        if local is None:
            return {"found": False, "reason": "LOCAL_ORDER_NOT_FOUND"}
        cancel_was_requested = local["state"] == OrderState.CANCEL_REQUESTED.value
        payload: dict[str, Any] | None = None
        try:
            payload = self.executor.backend.get_order_by_client_id(local["symbol"], local["client_order_id"])
        except Exception:
            payload = None
        rows = _rows(payload or {})
        if not rows and local.get("okx_order_id"):
            try:
                payload = self.executor.backend.get_order(local["symbol"], local["okx_order_id"])
            except Exception:
                payload = None
            rows = _rows(payload or {})
        if not rows:
            return {"found": False, "plan_id": plan_id, "state": local["state"]}
        result = self._apply_remote_order(local, rows[0])
        if cancel_was_requested and result.get("state") in {
            OrderState.SUBMITTED.value,
            OrderState.OPEN.value,
            OrderState.PARTIALLY_FILLED.value,
        }:
            current = self.store.order_for_plan(plan_id)
            if current is not None and current["state"] != OrderState.CANCEL_REQUESTED.value:
                self.store.transition_order(
                    plan_id,
                    OrderState.CANCEL_REQUESTED.value,
                    last_error="CANCEL_RECONCILIATION_REQUIRED",
                )
            return result | {
                "remote_state": result["state"],
                "state": OrderState.CANCEL_REQUESTED.value,
                "reason": "CANCEL_RECONCILIATION_REQUIRED",
            }
        return result

    def recover_active_orders(self) -> list[dict[str, Any]]:
        recovered = [self.reconcile_plan(order["plan_id"]) for order in self.store.active_orders()]
        recovered.extend(self.reconcile_managed_positions())
        return recovered

    @staticmethod
    def _fill_key(row: dict[str, Any]) -> str:
        identifier = row.get("tradeId") or row.get("billId") or row.get("fillId")
        if identifier not in (None, ""):
            return str(identifier)
        return "|".join(str(row.get(key, "")) for key in ("ordId", "fillTime", "fillSz", "fillPx", "fee"))

    @staticmethod
    def _page_cursor(row: dict[str, Any]) -> str | None:
        value = row.get("billId") or row.get("tradeId") or row.get("fillId")
        return str(value) if value not in (None, "") else None

    def _fill_pages(
        self,
        symbol: str,
        capabilities: dict[str, Any],
        *,
        order_id: str | None,
        begin_ms: int,
        end_ms: int,
        archive: bool,
    ) -> tuple[list[dict[str, Any]], bool]:
        limit = 20 if archive else 100
        after: str | None = None
        seen_cursors: set[str] = set()
        unique: dict[str, dict[str, Any]] = {}
        for _page in range(self.MAX_FILL_PAGES):
            payload = self.executor.backend.get_fills(
                symbol,
                after=after,
                order_id=order_id,
                begin_ms=begin_ms if capabilities.get("fills_time_window") else None,
                end_ms=end_ms if capabilities.get("fills_time_window") else None,
                archive=archive,
                limit=limit,
            )
            rows = _rows(payload)
            for row in rows:
                unique.setdefault(self._fill_key(row), row)
            if len(rows) < limit:
                return list(unique.values()), True
            if not capabilities.get("fills_pagination"):
                return list(unique.values()), False
            cursor = self._page_cursor(rows[-1]) if rows else None
            if not cursor or cursor in seen_cursors:
                return list(unique.values()), False
            seen_cursors.add(cursor)
            after = cursor
        return list(unique.values()), False

    def _load_exit_fills(
        self,
        symbol: str,
        begin_ms: int,
        protective_ids: set[str],
    ) -> tuple[list[dict[str, Any]], bool, str]:
        try:
            capabilities = self.executor.backend.capabilities()
        except Exception:
            return [], False, "RECOVERY_DATA_INSUFFICIENT"
        current = now_ms()
        age = max(0, current - begin_ms)
        archive = age > self.RECENT_FILL_WINDOW_MS
        if age > self.ARCHIVE_FILL_WINDOW_MS:
            return [], False, "EXIT_FILL_HISTORY_INCOMPLETE"
        if archive and not capabilities.get("fills_archive"):
            return [], False, "RECOVERY_DATA_INSUFFICIENT"
        if not capabilities.get("fill_order_lookup") and not capabilities.get("fills_pagination"):
            return [], False, "RECOVERY_DATA_INSUFFICIENT"

        complete = True
        rows: list[dict[str, Any]] = []
        try:
            if capabilities.get("fill_order_lookup"):
                for protective_id in sorted(protective_ids):
                    page_rows, page_complete = self._fill_pages(
                        symbol,
                        capabilities,
                        order_id=protective_id,
                        begin_ms=begin_ms,
                        end_ms=current,
                        archive=archive,
                    )
                    rows.extend(page_rows)
                    complete = complete and page_complete
            else:
                rows, complete = self._fill_pages(
                    symbol,
                    capabilities,
                    order_id=None,
                    begin_ms=begin_ms,
                    end_ms=current,
                    archive=archive,
                )
        except (NotImplementedError, RuntimeError, TimeoutError, ConnectionError):
            return [], False, "EXIT_FILL_DATA_UNAVAILABLE"

        unique = {self._fill_key(row): row for row in rows}
        matching = [
            row for row in unique.values()
            if str(row.get("side", "")).lower() == "sell"
            and str(row.get("ordId") or row.get("algoId") or "") in protective_ids
        ]
        matching.sort(key=lambda row: int(row.get("fillTime") or row.get("ts") or 0))
        return matching, complete, "PASS" if complete else "EXIT_FILL_HISTORY_INCOMPLETE"

    def _normalized_exit_fills(self, fills: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for row in fills:
            normalized.append({
                "fill_key": self._fill_key(row),
                "order_id": str(row.get("ordId") or row.get("algoId") or "") or None,
                "fill_timestamp_ms": int(row.get("fillTime") or row.get("ts") or 0),
                "quantity": float(row.get("fillSz") or row.get("sz") or 0),
                "price": float(row.get("fillPx") or row.get("px") or 0),
                "fee": abs(float(row["fee"])) if row.get("fee") not in (None, "") else None,
                "fee_currency": str(row.get("feeCcy") or "UNKNOWN"),
            })
        return normalized

    @staticmethod
    def _fee_breakdown(fills: list[dict[str, Any]]) -> tuple[dict[str, float], bool]:
        breakdown: dict[str, float] = {}
        complete = True
        for row in fills:
            value = row.get("fee")
            if value in (None, ""):
                complete = False
                continue
            currency = str(row.get("feeCcy") or "UNKNOWN")
            breakdown[currency] = breakdown.get(currency, 0.0) + abs(float(value))
        return breakdown, complete

    def _record_reconciliation(self, result: dict[str, Any], symbol: str) -> dict[str, Any]:
        code = str(result.get("reason") or result.get("state") or "UNKNOWN")
        self.store.record_reconciliation_event(
            str(result.get("plan_id")) if result.get("plan_id") else None,
            symbol,
            code,
            {key: value for key, value in result.items() if key not in {"raw", "response"}},
        )
        return result

    def reconcile_managed_positions(self) -> list[dict[str, Any]]:
        """Close managed positions only from fills linked to persisted protective order IDs."""
        results: list[dict[str, Any]] = []
        for position in self.store.managed_positions():
            try:
                protective_ids = set(json.loads(position.protective_order_ids_json or "[]"))
            except (TypeError, ValueError, json.JSONDecodeError):
                protective_ids = set()
            if not protective_ids:
                results.append(self._record_reconciliation(
                    {"plan_id": position.plan_id, "found": False,
                     "reason": "PROTECTIVE_ORDER_LINK_UNAVAILABLE"},
                    position.symbol,
                ))
                continue
            cursor_kind = f"protective_exit_fills:{position.plan_id}"
            cursor = self.store.reconciliation_cursor(position.symbol, cursor_kind)
            begin_ms = max(
                position.opened_at_ms,
                int(cursor.get("last_timestamp_ms") or position.opened_at_ms) if cursor else position.opened_at_ms,
            )
            fills, history_complete, history_reason = self._load_exit_fills(
                position.symbol,
                begin_ms,
                protective_ids,
            )
            if not history_complete:
                results.append(self._record_reconciliation(
                    {"plan_id": position.plan_id, "found": bool(fills),
                     "reason": history_reason, "state": position.state},
                    position.symbol,
                ))
                continue
            normalized = self._normalized_exit_fills(fills)
            self.store.record_reconciled_exit_fills(position.plan_id, normalized)
            if normalized:
                newest = max(normalized, key=lambda row: int(row["fill_timestamp_ms"]))
                self.store.update_reconciliation_cursor(
                    position.symbol,
                    cursor_kind,
                    int(newest["fill_timestamp_ms"]),
                    str(newest["fill_key"]),
                )
            persisted = self.store.reconciled_exit_fills(position.plan_id)
            quantity = sum(float(row["quantity"]) for row in persisted)
            fee_breakdown, fees_complete = self._fee_breakdown([
                {"fee": row["fee"], "feeCcy": row["fee_currency"]}
                for row in persisted
            ])
            if quantity + 1e-12 < position.quantity:
                if persisted:
                    self.store.mark_managed_partial_exit(position.plan_id, quantity, fee_breakdown)
                results.append(self._record_reconciliation(
                    {"plan_id": position.plan_id, "found": bool(persisted),
                     "state": "EXIT_PARTIALLY_FILLED" if persisted else position.state,
                     "filled_size": quantity, "fee_breakdown": fee_breakdown},
                    position.symbol,
                ))
                continue
            weighted = sum(
                float(row["price"]) * float(row["quantity"]) for row in persisted
            )
            exit_price = weighted / quantity if quantity > 0 else 0
            quote_currency = position.symbol.split("-")[-1].upper()
            exit_fees = (
                fee_breakdown[quote_currency]
                if fees_complete and set(fee_breakdown) == {quote_currency}
                else None
            )
            conversion_required = fees_complete and bool(fee_breakdown) and set(fee_breakdown) != {quote_currency}
            timestamps = [int(row["fill_timestamp_ms"]) for row in persisted]
            exit_order_id = str(persisted[-1].get("order_id") or "") or None
            self.store.close_trade(
                position.plan_id,
                max(timestamps),
                exit_price,
                exit_fees,
                exit_order_id,
                exit_filled_quantity=quantity,
                exit_fee_breakdown=fee_breakdown,
                exit_fee_conversion_required=conversion_required,
            )
            results.append(self._record_reconciliation(
                {"plan_id": position.plan_id, "found": True,
                 "state": OrderState.CLOSED.value, "exit_order_id": exit_order_id,
                 "fee_breakdown": fee_breakdown,
                 "fee_status": (
                     "ACTUAL" if exit_fees is not None
                     else "CURRENCY_CONVERSION_REQUIRED" if conversion_required
                     else "UNKNOWN"
                 )},
                position.symbol,
            ))
        return results

    def cancel_pending_entries(self) -> list[dict[str, Any]]:
        """Cancel only Agent entry orders; attached protection orders are never touched here."""
        results: list[dict[str, Any]] = []
        pending_states = {
            OrderState.APPROVED.value,
            OrderState.SUBMITTED.value,
            OrderState.SUBMISSION_UNKNOWN.value,
            OrderState.CANCEL_REQUESTED.value,
            OrderState.OPEN.value,
            OrderState.PARTIALLY_FILLED.value,
        }
        for original in self.store.active_orders():
            if original["state"] not in pending_states:
                continue
            plan_id = str(original["plan_id"])
            if original["state"] == OrderState.APPROVED.value:
                self.store.transition_order(
                    plan_id, OrderState.REJECTED.value, last_error="SESSION_BLOCKED_BEFORE_SUBMIT",
                )
                results.append({"plan_id": plan_id, "state": "ENTRY_BLOCKED_LOCAL"})
                continue
            if original["state"] == OrderState.SUBMISSION_UNKNOWN.value:
                self.reconcile_plan(plan_id)
            local = self.store.order_for_plan(plan_id) or original
            if local["state"] not in pending_states:
                continue
            if local["state"] == OrderState.CANCEL_REQUESTED.value:
                results.append(self._reconcile_cancel_request(plan_id))
                continue
            order_id = str(local.get("okx_order_id") or "")
            if not order_id:
                results.append({
                    "plan_id": plan_id,
                    "state": local["state"],
                    "reason": "ENTRY_CANCEL_REQUIRES_RECONCILIATION",
                })
                continue
            try:
                payload = self.executor.backend.cancel_order(local["symbol"], order_id)
                row = _rows(payload)[0] if _rows(payload) else {}
                if str(row.get("sCode", "0")) not in {"", "0"}:
                    raise RuntimeError("CANCEL_REJECTED")
                self.store.transition_order(
                    plan_id,
                    OrderState.CANCEL_REQUESTED.value,
                    raw_response_json=json.dumps(payload, default=str),
                )
                results.append(self._reconcile_cancel_request(plan_id))
            except Exception as exc:
                results.append({
                    "plan_id": plan_id,
                    "state": local["state"],
                    "reason": type(exc).__name__,
                })
        return results

    def _reconcile_cancel_request(self, plan_id: str) -> dict[str, Any]:
        result = self.targeted_reconciler.run(
            lambda: self.reconcile_plan(plan_id),
            lambda item: (
                item.get("state") in {
                    OrderState.CANCELLED.value,
                    OrderState.REJECTED.value,
                    OrderState.FILLED.value,
                }
                and item.get("managed_position_state")
                != OrderState.POSITION_UNPROTECTED.value
            ),
        )
        state = str(result.get("state") or "")
        if (
            state == OrderState.POSITION_UNPROTECTED.value
            or result.get("managed_position_state") == OrderState.POSITION_UNPROTECTED.value
        ):
            return result | {"reason": "POSITION_UNPROTECTED"}
        if state in {
            OrderState.CANCELLED.value,
            OrderState.REJECTED.value,
            OrderState.FILLED.value,
        }:
            return result
        local = self.store.order_for_plan(plan_id)
        if local and local["state"] in {
            OrderState.SUBMITTED.value,
            OrderState.OPEN.value,
            OrderState.PARTIALLY_FILLED.value,
            OrderState.CANCEL_REQUESTED.value,
        }:
            self.store.transition_order(
                plan_id,
                OrderState.CANCEL_REQUESTED.value,
                last_error="CANCEL_RECONCILIATION_REQUIRED",
            )
        return result | {
            "plan_id": plan_id,
            "state": OrderState.CANCEL_REQUESTED.value,
            "reason": "CANCEL_RECONCILIATION_REQUIRED",
        }

    @staticmethod
    def flatten_client_order_id(plan_id: str, attempt_number: int = 1) -> str:
        digest = hashlib.sha256(f"{plan_id}:{attempt_number}".encode("utf-8")).hexdigest()[:24]
        return f"flat-{digest}"

    def flatten_managed_positions(self) -> list[dict[str, Any]]:
        """Advance persisted close attempts without ever retrying a nonterminal attempt."""
        results: list[dict[str, Any]] = []
        for attempt in self.store.flatten_intents(active_only=True):
            if attempt["state"] == "PROTECTION_CLEANUP_INCOMPLETE":
                results.append(self.reconcile_flatten_intent(
                    str(attempt["plan_id"]), int(attempt["attempt_number"]),
                ))
        for position in self.store.managed_positions():
            attempts = self.store.flatten_attempts_for_plan(position.plan_id)
            latest_result: dict[str, Any] | None = None
            for attempt in attempts:
                latest_result = self.reconcile_flatten_intent(
                    position.plan_id, int(attempt["attempt_number"]),
                )
            active_position = next(
                (
                    item for item in self.store.managed_positions()
                    if item.plan_id == position.plan_id
                ),
                None,
            )
            if active_position is None:
                results.append(latest_result or {
                    "plan_id": position.plan_id, "state": OrderState.FILLED.value,
                })
                continue
            position = active_position
            attempts = self.store.flatten_attempts_for_plan(position.plan_id)
            latest = attempts[-1] if attempts else None
            confirmed_fills = self.store.reconciled_exit_fills(position.plan_id)
            confirmed_quantity = sum(float(item["quantity"]) for item in confirmed_fills)
            if latest is not None and latest["state"] not in {
                OrderState.FILLED.value,
                OrderState.CANCELLED.value,
                OrderState.REJECTED.value,
                "FAILED",
                "PROTECTION_CLEANUP_INCOMPLETE",
            }:
                active_unfilled = max(
                    0.0,
                    float(latest["requested_quantity"]) - float(latest["filled_quantity"]),
                )
                if confirmed_quantity + active_unfilled > position.quantity + 1e-12:
                    results.append(self._cancel_flatten_attempt(latest))
                    continue
            if latest is not None and not (
                bool(latest["terminal_confirmed"])
                and bool(latest["reconciliation_complete"])
                and latest["state"] in {
                    OrderState.CANCELLED.value,
                    OrderState.REJECTED.value,
                    "FAILED",
                }
            ):
                results.append(latest_result or {
                    "plan_id": position.plan_id,
                    "state": latest["state"],
                    "reason": "FLATTEN_RECONCILIATION_REQUIRED",
                })
                continue
            remaining = max(0.0, position.quantity - confirmed_quantity)
            if remaining <= 1e-12:
                results.append(self._close_flatten_position(position, latest, confirmed_fills))
                continue
            attempt_number = int(latest["attempt_number"]) + 1 if latest else 1
            client_id = self.flatten_client_order_id(position.plan_id, attempt_number)
            intent = self.store.create_flatten_intent(
                position.plan_id, client_id, position.symbol, remaining, attempt_number,
            )
            executor = getattr(self.executor, "execute_managed_exit", None)
            if executor is None:
                self.store.update_flatten_intent(
                    position.plan_id, "REJECTED", attempt_number=attempt_number,
                    last_error="FLATTEN_EXECUTOR_UNAVAILABLE",
                )
                results.append({
                    "plan_id": position.plan_id,
                    "state": "REJECTED",
                    "reason": "FLATTEN_EXECUTOR_UNAVAILABLE",
                })
                continue
            try:
                payload = executor(position.symbol, remaining, client_id)
            except (SubmissionUncertainError, TimeoutError, ConnectionError) as exc:
                self.store.update_flatten_intent(
                    position.plan_id, OrderState.SUBMISSION_UNKNOWN.value,
                    attempt_number=attempt_number,
                    last_error=type(exc).__name__,
                )
                results.append(self._targeted_flatten_reconcile(position.plan_id, attempt_number))
                continue
            except Exception as exc:
                self.store.update_flatten_intent(
                    position.plan_id, "FAILED", attempt_number=attempt_number,
                    last_error=type(exc).__name__,
                )
                results.append({
                    "plan_id": position.plan_id,
                    "state": "FAILED",
                    "reason": type(exc).__name__,
                })
                continue
            row = _rows(payload)[0] if _rows(payload) else {}
            if str(row.get("sCode", "0")) not in {"", "0"}:
                self.store.update_flatten_intent(
                    position.plan_id, "REJECTED",
                    attempt_number=attempt_number,
                    raw_response_json=json.dumps(payload, default=str),
                    last_error="OKX_EXIT_REJECTED",
                    terminal_confirmed=True,
                    reconciliation_complete=True,
                )
                results.append({
                    "plan_id": position.plan_id,
                    "state": "REJECTED",
                    "reason": "OKX_EXIT_REJECTED",
                })
                continue
            order_id = str(row.get("ordId") or row.get("orderId") or "") or None
            self.store.update_flatten_intent(
                position.plan_id,
                OrderState.SUBMITTED.value,
                attempt_number=attempt_number,
                order_id=order_id,
                raw_response_json=json.dumps(payload, default=str),
            )
            reconciled = self._targeted_flatten_reconcile(position.plan_id, attempt_number)
            results.append(reconciled | {
                "client_order_id": client_id,
                "order_id": reconciled.get("order_id") or order_id,
                "attempt_number": intent["attempt_number"],
            })
        return results

    def _targeted_flatten_reconcile(
        self, plan_id: str, attempt_number: int, *, allow_close: bool = True,
    ) -> dict[str, Any]:
        return self.targeted_reconciler.run(
            lambda: self.reconcile_flatten_intent(
                plan_id, attempt_number, allow_close=allow_close,
            ),
            lambda item: item.get("state") in {
                OrderState.FILLED.value,
                OrderState.CANCELLED.value,
                OrderState.REJECTED.value,
                "PROTECTION_CLEANUP_INCOMPLETE",
            },
        )

    def _cancel_flatten_attempt(self, intent: dict[str, Any]) -> dict[str, Any]:
        plan_id = str(intent["plan_id"])
        attempt_number = int(intent["attempt_number"])
        if intent["state"] == OrderState.CANCEL_REQUESTED.value:
            return self._targeted_flatten_reconcile(
                plan_id, attempt_number, allow_close=False,
            )
        order_id = str(intent.get("order_id") or "")
        if not order_id:
            return {
                "plan_id": plan_id,
                "attempt_number": attempt_number,
                "state": intent["state"],
                "reason": "FLATTEN_OVERSELL_RISK_RECONCILIATION_REQUIRED",
            }
        try:
            payload = self.executor.backend.cancel_order(intent["symbol"], order_id)
            row = _rows(payload)[0] if _rows(payload) else {}
            if str(row.get("sCode", "0")) not in {"", "0"}:
                raise RuntimeError("CANCEL_REJECTED")
            self.store.update_flatten_intent(
                plan_id,
                OrderState.CANCEL_REQUESTED.value,
                attempt_number=attempt_number,
                raw_response_json=json.dumps(payload, default=str),
                last_error="FLATTEN_OVERSELL_RISK_CANCEL_REQUESTED",
            )
        except Exception as exc:
            return {
                "plan_id": plan_id,
                "attempt_number": attempt_number,
                "state": intent["state"],
                "reason": f"FLATTEN_OVERSELL_RISK:{type(exc).__name__}",
            }
        return self._targeted_flatten_reconcile(
            plan_id, attempt_number, allow_close=False,
        )

    def reconcile_flatten_intent(
        self,
        plan_id: str,
        attempt_number: int | None = None,
        *,
        allow_close: bool = True,
    ) -> dict[str, Any]:
        intent = (
            self.store.flatten_attempt(plan_id, attempt_number)
            if attempt_number is not None else self.store.flatten_intent(plan_id)
        )
        if intent is None:
            return {"plan_id": plan_id, "found": False, "reason": "FLATTEN_INTENT_NOT_FOUND"}
        attempt_number = int(intent["attempt_number"])
        if intent["state"] == "PROTECTION_CLEANUP_INCOMPLETE":
            return self._cleanup_flatten_protection(plan_id, attempt_number)
        if intent["state"] == "FILLED":
            return {
                "plan_id": plan_id, "found": True, "state": "FILLED",
                "attempt_number": attempt_number,
            }
        cancel_was_requested = intent["state"] == OrderState.CANCEL_REQUESTED.value
        payload: dict[str, Any] | None = None
        try:
            payload = self.executor.backend.get_order_by_client_id(
                intent["symbol"], intent["client_order_id"],
            )
        except Exception:
            payload = None
        rows = _rows(payload or {})
        if not rows and intent.get("order_id"):
            try:
                payload = self.executor.backend.get_order(intent["symbol"], intent["order_id"])
            except Exception:
                payload = None
            rows = _rows(payload or {})
        if not rows:
            return {
                "plan_id": plan_id,
                "found": False,
                "state": intent["state"],
                "reason": "FLATTEN_RECONCILIATION_REQUIRED",
            }
        row = rows[0]
        state, filled, average = _map_state(row, float(intent["requested_quantity"]))
        remote_state = state
        if cancel_was_requested and state in {
            OrderState.SUBMITTED.value,
            OrderState.OPEN.value,
            OrderState.PARTIALLY_FILLED.value,
        }:
            state = OrderState.CANCEL_REQUESTED.value
        order_id = str(row.get("ordId") or intent.get("order_id") or "") or None
        self.store.update_flatten_intent(
            plan_id, state, attempt_number=attempt_number,
            filled_quantity=filled, order_id=order_id,
            raw_response_json=json.dumps(row, default=str),
            terminal_confirmed=state in {
                OrderState.FILLED.value, OrderState.CANCELLED.value, OrderState.REJECTED.value,
            },
            reconciliation_complete=(
                state in {OrderState.FILLED.value, OrderState.CANCELLED.value, OrderState.REJECTED.value}
                and (filled <= 1e-12 or average is not None)
            ),
        )
        if filled > 0 and average is not None:
            self.store.upsert_reconciled_exit_fill(
                plan_id,
                f"flatten-attempt:{attempt_number}",
                order_id,
                int(row.get("fillTime") or row.get("uTime") or now_ms()),
                filled,
                average,
            )
        confirmed_fills = self.store.reconciled_exit_fills(plan_id)
        confirmed_quantity = sum(float(item["quantity"]) for item in confirmed_fills)
        position = next(
            (item for item in self.store.managed_positions() if item.plan_id == plan_id),
            None,
        )
        if position is not None and 0 < confirmed_quantity < position.quantity:
            self.store.mark_managed_partial_exit(plan_id, confirmed_quantity, {})
        if position is not None and confirmed_quantity + 1e-12 >= position.quantity:
            if not allow_close:
                return {
                    "plan_id": plan_id,
                    "found": True,
                    "state": state,
                    "filled_quantity": filled,
                    "order_id": order_id,
                    "attempt_number": attempt_number,
                    "confirmed_exit_quantity": confirmed_quantity,
                    "remote_state": remote_state,
                }
            active_others = [
                item for item in self.store.flatten_attempts_for_plan(plan_id)
                if int(item["attempt_number"]) != attempt_number
                and item["state"] not in {
                    OrderState.FILLED.value,
                    OrderState.CANCELLED.value,
                    OrderState.REJECTED.value,
                    "FAILED",
                    "PROTECTION_CLEANUP_INCOMPLETE",
                }
            ]
            for other in active_others:
                cancel_result = self._cancel_flatten_attempt(other)
                if cancel_result.get("state") not in {
                    OrderState.FILLED.value,
                    OrderState.CANCELLED.value,
                    OrderState.REJECTED.value,
                }:
                    return {
                        "plan_id": plan_id,
                        "found": True,
                        "state": "FLATTEN_INCOMPLETE",
                        "reason": "ACTIVE_EXIT_CANCEL_RECONCILIATION_REQUIRED",
                        "attempt_number": attempt_number,
                        "confirmed_exit_quantity": confirmed_quantity,
                    }
            confirmed_fills = self.store.reconciled_exit_fills(plan_id)
            confirmed_quantity = sum(float(item["quantity"]) for item in confirmed_fills)
            if confirmed_quantity > position.quantity + 1e-12:
                self.store.update_flatten_intent(
                    plan_id,
                    "RECONCILIATION_FAILED",
                    attempt_number=attempt_number,
                    last_error="CONFIRMED_EXIT_EXCEEDS_MANAGED_QUANTITY",
                )
                return {
                    "plan_id": plan_id,
                    "found": True,
                    "state": "RECONCILIATION_FAILED",
                    "reason": "CONFIRMED_EXIT_EXCEEDS_MANAGED_QUANTITY",
                    "attempt_number": attempt_number,
                    "confirmed_exit_quantity": confirmed_quantity,
                }
            return self._close_flatten_position(position, intent, confirmed_fills)
        if state != OrderState.FILLED.value or average is None:
            return {
                "plan_id": plan_id,
                "found": True,
                "state": state,
                "filled_quantity": filled,
                "order_id": order_id,
                "attempt_number": attempt_number,
                "confirmed_exit_quantity": confirmed_quantity,
                "remote_state": remote_state,
            }
        return {
            "plan_id": plan_id,
            "found": True,
            "state": "RECONCILIATION_REQUIRED",
            "attempt_number": attempt_number,
        }

    def _close_flatten_position(
        self, position: Any, intent: dict[str, Any] | None, fills: list[dict[str, Any]],
    ) -> dict[str, Any]:
        plan_id = position.plan_id
        if not fills:
            return {"plan_id": plan_id, "state": "RECONCILIATION_REQUIRED"}
        quantity = sum(float(item["quantity"]) for item in fills)
        if quantity + 1e-12 < position.quantity:
            return {
                "plan_id": plan_id, "state": "FLATTEN_INCOMPLETE",
                "confirmed_exit_quantity": quantity,
            }
        weighted = sum(float(item["price"]) * float(item["quantity"]) for item in fills)
        average = weighted / quantity
        last_fill = max(fills, key=lambda item: int(item["fill_timestamp_ms"]))
        attempt_number = int((intent or self.store.flatten_intent(plan_id))["attempt_number"])
        try:
            self.store.close_trade(
                plan_id,
                int(last_fill["fill_timestamp_ms"]),
                average,
                None,
                str(last_fill.get("order_id") or "") or None,
                exit_filled_quantity=quantity,
                exit_fee_breakdown={},
            )
        except Exception as exc:
            self.store.update_flatten_intent(
                plan_id, "RECONCILIATION_FAILED", attempt_number=attempt_number,
                last_error=type(exc).__name__,
            )
            return {
                "plan_id": plan_id,
                "found": True,
                "state": "RECONCILIATION_FAILED",
                "reason": type(exc).__name__,
            }
        return self._cleanup_flatten_protection(plan_id, attempt_number)

    def _cleanup_flatten_protection(
        self, plan_id: str, attempt_number: int,
    ) -> dict[str, Any]:
        attempt = self.store.flatten_attempt(plan_id, attempt_number)
        if attempt is None:
            return {
                "plan_id": plan_id,
                "state": "RECONCILIATION_REQUIRED",
                "reason": "FLATTEN_ATTEMPT_NOT_FOUND",
            }
        position = next(
            (
                item for item in self.store.managed_positions(active_only=False)
                if item.plan_id == plan_id
            ),
            None,
        )
        if position is None or position.state != OrderState.CLOSED.value:
            return {
                "plan_id": plan_id, "state": "RECONCILIATION_REQUIRED",
                "reason": "POSITION_NOT_CONFIRMED_CLOSED",
            }
        try:
            persisted_cleanup = json.loads(attempt.get("protection_cleanup_json") or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            persisted_cleanup = []
        cleanup_by_id = {
            str(item.get("order_id")): item
            for item in persisted_cleanup
            if isinstance(item, dict) and item.get("order_id")
        }
        protection_ids_valid = True
        try:
            protective_ids = json.loads(position.protective_order_ids_json or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            protection_ids_valid = False
            protective_ids = []
        for protective_id in protective_ids:
            identifier = str(protective_id)
            if cleanup_by_id.get(identifier, {}).get("state") == "CANCELLED":
                continue
            try:
                self.executor.backend.cancel_protection_order(position.symbol, identifier)
                cleanup_by_id[identifier] = {"order_id": identifier, "state": "CANCELLED"}
            except Exception as exc:
                cleanup_by_id[identifier] = {
                    "order_id": identifier,
                    "state": type(exc).__name__,
                }
        cleanup = [cleanup_by_id[str(item)] for item in protective_ids]
        complete = (
            protection_ids_valid
            and not (position.protection_state == "PROTECTED" and not protective_ids)
            and all(item["state"] == "CANCELLED" for item in cleanup)
        )
        final_state = "FILLED" if complete else "PROTECTION_CLEANUP_INCOMPLETE"
        self.store.update_flatten_intent(
            plan_id, final_state, attempt_number=attempt_number,
            terminal_confirmed=True, reconciliation_complete=complete,
            last_error=None if complete else "PROTECTION_CLEANUP_INCOMPLETE",
            protection_cleanup_json=json.dumps(cleanup, sort_keys=True),
        )
        return {
            "plan_id": plan_id,
            "found": True,
            "state": final_state,
            "attempt_number": attempt_number,
            "protection_cleanup": cleanup,
        }

    def query(self, symbol: str, order_id: str) -> dict[str, Any]:
        return self.executor.backend.get_order(symbol, order_id)

    def cancel(self, plan_id: str, approval_text: str) -> dict[str, Any]:
        if approval_text.strip() not in EXPLICIT_APPROVALS:
            raise PermissionError("EXPLICIT_USER_APPROVAL_REQUIRED")
        local = self.store.order_for_plan(plan_id)
        if not local or not local.get("okx_order_id"):
            raise LookupError("ORDER_NOT_CANCELLABLE")
        payload = self.executor.backend.cancel_order(local["symbol"], local["okx_order_id"])
        row = _rows(payload)[0] if _rows(payload) else {}
        if str(row.get("sCode", "0")) not in {"", "0"}:
            raise RuntimeError("CANCEL_REJECTED")
        self.store.transition_order(
            plan_id,
            OrderState.CANCEL_REQUESTED.value,
            raw_response_json=json.dumps(payload, default=str),
        )
        return self._reconcile_cancel_request(plan_id) | {"cancel_response": payload}
