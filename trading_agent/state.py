from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RuntimeMode(str, Enum):
    STOPPED = "STOPPED"
    DRY_RUN = "DRY_RUN"
    MANUAL_APPROVAL = "MANUAL_APPROVAL"
    AUTO_DEMO = "AUTO_DEMO"


@dataclass
class AgentState:
    environment: str = "demo"
    backend: str = "mcp"
    last_symbol: str | None = None
    last_plan_id: str | None = None
    runtime_mode: RuntimeMode = RuntimeMode.MANUAL_APPROVAL
    auto_demo_enabled: bool = False
    execution_armed: bool = True
    kill_switch_active: bool = False

    def set_mode(self, mode: RuntimeMode) -> None:
        if mode is RuntimeMode.AUTO_DEMO and not self.auto_demo_enabled:
            raise PermissionError("AUTO_DEMO_DISABLED")
        self.runtime_mode = mode

    @property
    def allows_new_entries(self) -> bool:
        return (
            self.execution_armed
            and not self.kill_switch_active
            and self.runtime_mode in {RuntimeMode.MANUAL_APPROVAL, RuntimeMode.AUTO_DEMO}
        )

    def require_new_entry_allowed(self) -> None:
        if not self.allows_new_entries:
            if self.kill_switch_active:
                raise PermissionError("KILL_SWITCH_ACTIVE")
            if not self.execution_armed:
                raise PermissionError("EXECUTION_DISARMED")
            raise PermissionError("TRADING_STOPPED" if self.runtime_mode is RuntimeMode.STOPPED else "TRADING_NOT_EXECUTABLE")

    def arm_execution(self) -> None:
        if self.kill_switch_active:
            raise PermissionError("KILL_SWITCH_ACTIVE")
        self.execution_armed = True

    def disarm_execution(self) -> None:
        self.execution_armed = False

    def activate_kill_switch(self) -> None:
        self.kill_switch_active = True
        self.execution_armed = False
        self.runtime_mode = RuntimeMode.STOPPED

    def reset_kill_switch(self) -> None:
        self.kill_switch_active = False
        self.execution_armed = False
