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

    def set_mode(self, mode: RuntimeMode) -> None:
        if mode is RuntimeMode.AUTO_DEMO and not self.auto_demo_enabled:
            raise PermissionError("AUTO_DEMO_DISABLED")
        self.runtime_mode = mode
