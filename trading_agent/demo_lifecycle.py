from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from execution.errors import PreSubmitRejectedError, SubmissionUncertainError, sanitize_diagnostic_value
from execution.order_state import OrderState
from risk.position_sizing import minimum_executable_quantity
from trading_agent.orchestrator import TradingOrchestrator
from trading_agent.service import sanitize_for_browser

REAL_DEMO_CONFIRMATION = "I APPROVE ONE REAL OKX DEMO LIFECYCLE TEST"


def submission_gate(
    *,
    submit_flag: bool,
    confirmation: str,
    environment: str,
    backend_available: bool,
    backend_demo: bool,
    attached_tp_sl: bool,
    precheck_ready: bool,
) -> tuple[bool, str]:
    checks = (
        (submit_flag, "REAL_DEMO_SUBMIT_FLAG_REQUIRED"),
        (confirmation.strip() == REAL_DEMO_CONFIRMATION, "EXACT_REAL_DEMO_CONFIRMATION_REQUIRED"),
        (environment.lower() == "demo", "DEMO_ENVIRONMENT_REQUIRED"),
        (backend_available and backend_demo, "VERIFIED_DEMO_BACKEND_REQUIRED"),
        (attached_tp_sl, "TP_SL_BACKEND_NOT_SUPPORTED"),
        (precheck_ready, "PRECHECK_NOT_READY"),
    )
    return next(((False, reason) for passed, reason in checks if not passed), (True, "PASS"))


class DemoLifecycleVerifier:
    """Controlled Demo lifecycle harness; submission is impossible without the exact dual opt-in."""

    def __init__(self, orchestrator: TradingOrchestrator, export_root: Path) -> None:
        self.orchestrator = orchestrator
        self.export_root = export_root

    @staticmethod
    def _event(timeline: list[dict[str, Any]], state: str, details: dict[str, Any] | None = None) -> None:
        timeline.append({
            "timestamp_ms": int(time.time() * 1000),
            "state": state,
            "details": sanitize_for_browser(details or {}),
        })

    def _export(self, payload: dict[str, Any]) -> Path:
        self.export_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        plan_id = str(payload.get("plan_id") or "no-plan")
        path = self.export_root / f"demo_lifecycle_{stamp}_{plan_id}.json"
        path.write_text(
            json.dumps(sanitize_for_browser(payload), indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def _exception_chain(exc: BaseException) -> list[BaseException]:
        chain: list[BaseException] = []
        current: BaseException | None = exc
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            chain.append(current)
            current = current.__cause__ or current.__context__
        return chain

    @classmethod
    def _classify_submit_failure(cls, exc: BaseException) -> tuple[str, str, dict[str, Any]]:
        chain = cls._exception_chain(exc)
        explicit = next((item for item in chain if isinstance(item, PreSubmitRejectedError)), None)
        uncertain = next((item for item in chain if isinstance(item, SubmissionUncertainError)), None)
        messages = [str(item) for item in chain]
        if uncertain is not None or any(message.startswith("SUBMISSION_UNKNOWN") for message in messages):
            return "SUBMISSION_UNKNOWN", "SUBMISSION_UNKNOWN_RECONCILIATION_REQUIRED", {}
        if explicit is not None:
            diagnostics = sanitize_diagnostic_value(explicit.diagnostics)
            return "SUBMIT_REJECTED", explicit.reason, diagnostics
        explicit_message = next(
            (
                message
                for message in messages
                if message.startswith(("PRE_SUBMIT_REJECTED", "OKX_ORDER_REJECTED"))
            ),
            None,
        )
        if explicit_message:
            return "SUBMIT_REJECTED", explicit_message.split(":", 1)[0], {}
        safe_message = sanitize_diagnostic_value(str(exc))
        return "SUBMIT_FAILED", type(exc).__name__, {"message": safe_message}

    def precheck(self, symbol: str) -> dict[str, Any]:
        symbol = symbol.upper()
        timeline: list[dict[str, Any]] = []
        backend = self.orchestrator.adapter.backend
        status = backend.status()
        capabilities = backend.capabilities()
        health = self.orchestrator.get_health()
        market = self.orchestrator.market_data.get_snapshot(symbol)
        account = self.orchestrator.account_data.get_snapshot(symbol)
        plan = self.orchestrator.analyze(symbol)
        self._event(timeline, "PLAN_CREATED", {"plan_id": plan.plan_id, "decision": plan.decision})
        preview = self.orchestrator.approve_plan(plan.plan_id)
        self._event(timeline, "PREVIEW_REVALIDATED", preview)
        minimum_quantity = minimum_executable_quantity(market.instrument)
        estimated_notional = minimum_quantity * float(preview.get("current_executable_price") or market.ask)
        blockers = list(health.get("trading_eligibility", {}).get("blocking_reasons", []))
        blockers = [item for item in blockers if item not in {"EXECUTION_DISARMED", "TRADING_STOPPED"}]
        ready = bool(
            status.available
            and status.demo
            and capabilities.get("attached_tp_sl")
            and plan.decision == "BUY"
            and plan.risk_approved
            and preview.get("status") == "READY_FOR_EXACT_APPROVAL"
            and not blockers
            and minimum_quantity <= float(preview.get("position_size") or 0)
        )
        report = {
            "mode": "PRECHECK_READ_ONLY",
            "place_order_called": False,
            "environment": self.orchestrator.config.environment,
            "backend_status": asdict(status),
            "capabilities": capabilities,
            "health": health,
            "account": {
                "timestamp_ms": account.timestamp_ms,
                "equity_usdt": account.equity_usdt,
                "available_usdt": account.available_usdt,
            },
            "market": {
                "symbol": symbol,
                "timestamp_ms": market.timestamp_ms,
                "bid": market.bid,
                "ask": market.ask,
                "spread_pct": market.spread_pct,
                "minSz": market.instrument.min_size,
                "lotSz": market.instrument.lot_size,
                "tickSz": market.instrument.tick_size,
            },
            "plan_id": plan.plan_id,
            "plan": plan.as_dict(),
            "preview": preview,
            "minimum_executable_quantity": minimum_quantity,
            "minimum_estimated_notional_usdt": estimated_notional,
            "blocking_reasons": blockers,
            "ready_for_explicit_real_demo_submission": ready,
            "timeline": timeline,
            "restart_recovery_command": (
                f"uv run python scripts/verify_demo_lifecycle.py --resume-plan {plan.plan_id}"
            ),
        }
        export_path = self._export(report)
        return sanitize_for_browser(report | {"audit_export": str(export_path), "preview_exported": True})

    def submit(self, precheck: dict[str, Any], *, submit_flag: bool, confirmation: str) -> dict[str, Any]:
        backend = self.orchestrator.adapter.backend
        status = backend.status()
        capabilities = backend.capabilities()
        allowed, reason = submission_gate(
            submit_flag=submit_flag,
            confirmation=confirmation,
            environment=self.orchestrator.config.environment,
            backend_available=status.available,
            backend_demo=status.demo,
            attached_tp_sl=bool(capabilities.get("attached_tp_sl")),
            precheck_ready=bool(precheck.get("ready_for_explicit_real_demo_submission"))
            and bool(precheck.get("preview_exported")),
        )
        if not allowed:
            raise PermissionError(reason)
        plan_id = str(precheck["plan_id"])
        timeline = list(precheck.get("timeline", []))
        self._event(timeline, "USER_EXPLICITLY_APPROVES_REAL_DEMO_TEST")
        self._event(timeline, "SUBMIT_ATTEMPTED", {"plan_id": plan_id})
        try:
            result = self.orchestrator.approve_plan(
                plan_id,
                "CONFIRM DEMO ORDER",
                position_size_cap=float(precheck["minimum_executable_quantity"]),
            )
        except Exception as exc:
            failure_status, failure_reason, diagnostics = self._classify_submit_failure(exc)
            self._event(timeline, failure_status, {
                "reason": failure_reason,
                "diagnostics": diagnostics,
            })
            failure = sanitize_for_browser({
                "mode": "REAL_DEMO_SUBMISSION_EXPLICITLY_APPROVED",
                "status": failure_status,
                "reason": failure_reason,
                "diagnostics": diagnostics,
                "plan_id": plan_id,
                "result": {
                    "status": failure_status,
                    "reason": failure_reason,
                    "diagnostics": diagnostics,
                },
                "timeline": timeline,
                "restart_recovery_command": precheck["restart_recovery_command"],
                "automatic_cancellation": False,
                "protection_orders_preserved": True,
            })
            export_path = self._export(failure)
            failure_with_export = failure | {"audit_export": str(export_path)}
            try:
                setattr(exc, "failure_audit", failure_with_export)
            except Exception:
                pass
            raise
        self._event(timeline, "SUBMIT_SUCCEEDED", result)
        output = sanitize_for_browser({
            "mode": "REAL_DEMO_SUBMISSION_EXPLICITLY_APPROVED",
            "status": "SUBMIT_SUCCEEDED",
            "plan_id": plan_id,
            "result": result,
            "timeline": timeline,
            "restart_recovery_command": precheck["restart_recovery_command"],
            "automatic_cancellation": False,
            "protection_orders_preserved": True,
        })
        export_path = self._export(output)
        return output | {"audit_export": str(export_path)}

    def resume(self, plan_id: str) -> dict[str, Any]:
        timeline: list[dict[str, Any]] = []
        order = self.orchestrator.order_manager.reconcile_plan(plan_id)
        self._event(timeline, str(order.get("state") or order.get("reason") or "RECONCILED"), order)
        positions = self.orchestrator.order_manager.reconcile_managed_positions()
        matching = [item for item in positions if item.get("plan_id") == plan_id]
        for item in matching:
            self._event(timeline, str(item.get("state") or item.get("reason") or "RECONCILED"), item)
        result = {
            "mode": "RESUME_RECONCILIATION_READ_ONLY",
            "place_order_called": False,
            "plan_id": plan_id,
            "order": order,
            "managed_position_results": matching,
            "timeline": timeline,
            "terminal": any(item.get("state") == OrderState.CLOSED.value for item in matching),
        }
        export_path = self._export(result)
        return sanitize_for_browser(result | {"audit_export": str(export_path)})
