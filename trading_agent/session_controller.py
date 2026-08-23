from __future__ import annotations

from typing import TYPE_CHECKING, Any

from trading_agent.control_state import AgentRuntimeState, ExecutionState, SessionState, TradingMode

if TYPE_CHECKING:
    from trading_agent.service import TradingService


class SessionPreflightError(RuntimeError):
    def __init__(self, blockers: list[str]) -> None:
        super().__init__(blockers[0] if blockers else "SESSION_PREFLIGHT_FAILED")
        self.blockers = blockers or ["SESSION_PREFLIGHT_FAILED"]


class AutoTradingSessionController:
    """The single owner of user-level START, PAUSE, STOP and FLATTEN semantics."""

    def __init__(self, service: TradingService) -> None:
        self.service = service
        self.last_preflight: dict[str, Any] = {"passed": False, "blockers": ["NOT_RUN"]}

    def start(self) -> dict[str, Any]:
        current = self.service._control.get()
        if current.session_state is SessionState.RUNNING:
            return self.status() | {"idempotent": True}
        blockers = self.service._session_preflight()
        self.last_preflight = {"passed": not blockers, "blockers": blockers}
        if blockers:
            raise SessionPreflightError(blockers)
        control = self.service._transition(
            "START_AUTO_SESSION",
            reason="SESSION_LEVEL_AUTHORIZATION_ACCEPTED",
            session_state=SessionState.RUNNING,
            agent_runtime_state=AgentRuntimeState.RUNNING,
            trading_mode=TradingMode.AUTO,
            auto_demo_enabled=True,
            execution_state=ExecutionState.ARMED,
        )
        return self.status() | {"control": control, "idempotent": False}

    def pause(self) -> dict[str, Any]:
        current = self.service._control.get()
        if current.session_state is SessionState.PAUSED:
            return self.status() | {"idempotent": True}
        control = self.service._transition(
            "PAUSE_AUTO_SESSION",
            reason="NEW_ENTRIES_BLOCKED_PROTECTION_PRESERVED",
            session_state=SessionState.PAUSED,
            agent_runtime_state=AgentRuntimeState.RUNNING,
            trading_mode=TradingMode.MANUAL_APPROVAL,
            auto_demo_enabled=False,
            execution_state=ExecutionState.DISARMED,
        )
        cancelled = self.service._run_core(self.service._orchestrator.cancel_pending_entries)
        return self.status() | {"control": control, "cancelled_entries": cancelled, "idempotent": False}

    def stop(self) -> dict[str, Any]:
        current = self.service._control.get()
        if current.session_state is SessionState.STOPPED:
            cancelled = self.service._run_core(self.service._orchestrator.cancel_pending_entries)
            return self.status() | {"cancelled_entries": cancelled, "idempotent": True}
        control = self.service._transition(
            "STOP_AUTO_SESSION",
            reason="SESSION_STOPPED_PROTECTION_PRESERVED",
            session_state=SessionState.STOPPED,
            agent_runtime_state=AgentRuntimeState.STOPPED,
            trading_mode=TradingMode.STOPPED,
            auto_demo_enabled=False,
            execution_state=ExecutionState.DISARMED,
        )
        cancelled = self.service._run_core(self.service._orchestrator.cancel_pending_entries)
        return self.status() | {"control": control, "cancelled_entries": cancelled, "idempotent": False}

    def flatten(self) -> dict[str, Any]:
        current = self.service._control.get()
        if current.session_state is not SessionState.FLATTENING:
            self.service._transition(
                "FLATTEN_ALL_AND_STOP",
                reason="NEW_ENTRIES_BLOCKED_BEFORE_MANAGED_EXIT",
                session_state=SessionState.FLATTENING,
                agent_runtime_state=AgentRuntimeState.RUNNING,
                trading_mode=TradingMode.MANUAL_APPROVAL,
                auto_demo_enabled=False,
                execution_state=ExecutionState.DISARMED,
            )
        self.service._run_core(self.service._orchestrator.recover)
        cancelled = self.service._run_core(self.service._orchestrator.cancel_pending_entries)
        exits = self.service._run_core(self.service._orchestrator.flatten_managed_positions)
        return self._finish_flatten(cancelled, exits)

    def continue_flatten(self) -> dict[str, Any]:
        if self.service._control.get().session_state is not SessionState.FLATTENING:
            return self.status()
        cancelled = self.service._run_core(self.service._orchestrator.cancel_pending_entries)
        exits = self.service._run_core(self.service._orchestrator.flatten_managed_positions)
        return self._finish_flatten(cancelled, exits)

    def _finish_flatten(
        self, cancelled_entries: list[dict[str, Any]], exits: list[dict[str, Any]],
    ) -> dict[str, Any]:
        remaining = self.service._run_core(self.service._orchestrator.trade_store.managed_positions)
        active_entries = self.service._run_core(
            self.service._orchestrator.trade_store.active_orders
        )
        active_flatten_attempts = self.service._run_core(
            self.service._orchestrator.trade_store.flatten_intents, True,
        )
        unresolved_states = {
            "APPROVED", "SUBMITTED", "SUBMISSION_UNKNOWN", "CANCEL_REQUESTED",
            "OPEN", "PARTIALLY_FILLED",
            "FILLED", "POSITION_UNPROTECTED",
        }
        unresolved_entries = [
            item for item in cancelled_entries
            if item.get("state") not in {"CANCELLED", "ENTRY_BLOCKED_LOCAL"}
        ]
        unresolved_entries.extend(
            {
                "plan_id": item.get("plan_id"),
                "state": item.get("state"),
                "reason": "AGENT_ENTRY_STILL_ACTIVE",
            }
            for item in active_entries
            if item.get("state") in unresolved_states
            and not any(
                existing.get("plan_id") == item.get("plan_id")
                for existing in unresolved_entries
            )
        )
        if not remaining and not unresolved_entries and not active_flatten_attempts:
            control = self.service._transition(
                "FLATTEN_CONFIRMED_FLAT",
                reason="MANAGED_EXPOSURE_ZERO",
                session_state=SessionState.STOPPED,
                agent_runtime_state=AgentRuntimeState.STOPPED,
                trading_mode=TradingMode.STOPPED,
                auto_demo_enabled=False,
                execution_state=ExecutionState.DISARMED,
            )
            return {
                "status": "FLAT",
                "session_state": SessionState.STOPPED.value,
                "managed_exposure_zero": True,
                "remaining_positions": [],
                "cancelled_entries": cancelled_entries,
                "unresolved_entries": [],
                "active_flatten_attempts": [],
                "exits": exits,
                "control": control,
            }
        return {
            "status": "FLATTEN_INCOMPLETE",
            "session_state": SessionState.FLATTENING.value,
            "managed_exposure_zero": False,
            "remaining_positions": [
                {
                    "plan_id": item.plan_id,
                    "symbol": item.symbol,
                    "quantity": item.quantity,
                    "exit_filled_quantity": item.exit_filled_quantity,
                }
                for item in remaining
            ],
            "cancelled_entries": cancelled_entries,
            "unresolved_entries": unresolved_entries,
            "active_flatten_attempts": active_flatten_attempts,
            "exits": exits,
        }

    def degrade(self, reason: str) -> dict[str, Any]:
        current = self.service._control.get()
        if current.session_state is not SessionState.RUNNING:
            return current.as_dict()
        return self.service._transition(
            "SESSION_FAIL_CLOSED",
            reason=reason,
            session_state=SessionState.DEGRADED,
            agent_runtime_state=AgentRuntimeState.DEGRADED,
            trading_mode=TradingMode.MANUAL_APPROVAL,
            auto_demo_enabled=False,
            execution_state=ExecutionState.DISARMED,
        )

    def status(self) -> dict[str, Any]:
        control = self.service._control.get().as_dict()
        return {
            "session_state": control["session_state"],
            "control": control,
            "preflight": dict(self.last_preflight),
        }
