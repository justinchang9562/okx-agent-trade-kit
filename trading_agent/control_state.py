from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class EnvironmentState(str, Enum):
    DEMO = "DEMO"
    LIVE = "LIVE"


class LiveSetupState(str, Enum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    READ_ONLY_READY = "READ_ONLY_READY"
    TRADE_PERMISSION_READY = "TRADE_PERMISSION_READY"


class ExecutionState(str, Enum):
    DISARMED = "DISARMED"
    ARMED = "ARMED"


class AgentRuntimeState(str, Enum):
    STOPPED = "STOPPED"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    STALE = "STALE"


class TradingMode(str, Enum):
    STOPPED = "STOPPED"
    DRY_RUN = "DRY_RUN"
    MANUAL_APPROVAL = "MANUAL_APPROVAL"
    AUTO = "AUTO"


class ConnectionState(str, Enum):
    CONNECTED = "CONNECTED"
    STALE = "STALE"
    DISCONNECTED = "DISCONNECTED"


@dataclass(frozen=True)
class ControlSnapshot:
    environment: EnvironmentState = EnvironmentState.DEMO
    live_setup_state: LiveSetupState = LiveSetupState.NOT_CONFIGURED
    execution_state: ExecutionState = ExecutionState.DISARMED
    agent_runtime_state: AgentRuntimeState = AgentRuntimeState.STOPPED
    trading_mode: TradingMode = TradingMode.STOPPED
    connection_state: ConnectionState = ConnectionState.DISCONNECTED
    kill_switch_active: bool = False
    auto_demo_enabled: bool = False
    scan_interval_seconds: float = 15.0
    updated_at_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
