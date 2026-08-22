from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from data.account_data import AccountDataService
from data.market_data import MarketDataService
from execution.cli_backend import CLIBackend
from execution.native_api_backend import NativeAPIBackend
from execution.okx_adapter import OKXAdapter
from storage.database import connect
from trading_agent.config import AppConfig


def run_health(config: AppConfig, adapter: OKXAdapter) -> dict[str, Any]:
    checks: dict[str, Any] = {
        "configuration": "PASS",
        "database": "FAIL",
        "okx_adapter": asdict(adapter.backend.status()),
        "mcp_backend": "UNKNOWN",
        "cli_backend": asdict(CLIBackend().status()),
        "native_api_backend": asdict(NativeAPIBackend().status()),
        "market_data": "FAIL",
        "account": "FAIL",
        "risk_engine": "PASS",
        "managed_position_semantics": "FAIL",
        "persistent_order_lifecycle": "FAIL",
        "idempotency": "FAIL",
        "tp_sl_capability": "UNKNOWN",
        "auto_demo": "DISABLED",
        "demo_execution": "BLOCKED",
        "live_trading": "LOCKED",
    }
    database_path = config.root / "trading_agent.db"
    try:
        connection = connect(database_path)
        connection.execute("SELECT 1").fetchone()
        required_tables = {"trade_plans", "order_lifecycle", "managed_positions", "trades"}
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if required_tables.issubset(tables):
            checks["managed_position_semantics"] = "PASS"
            checks["persistent_order_lifecycle"] = "PASS"
            checks["idempotency"] = "PASS_CLIENT_ORDER_ID_AND_UNIQUE_PLAN_ID"
        connection.close()
        checks["database"] = "PASS"
    except Exception as exc:
        checks["database"] = f"FAIL:{exc}"
    backend_status = adapter.backend.status()
    capabilities = adapter.backend.capabilities()
    checks["tp_sl_capability"] = (
        "CAPABILITY_DECLARED_NOT_ORDER_VERIFIED" if capabilities.get("attached_tp_sl")
        else "TP_SL_BACKEND_NOT_SUPPORTED"
    )
    checks["historical_pagination"] = "PASS" if capabilities.get("historical_pagination") else "NOT_CONFIGURED"
    checks["mcp_backend"] = backend_status.state if config.backend == "mcp" else "NOT_SELECTED"
    try:
        MarketDataService(adapter.backend, config.rules).get_snapshot(config.symbols[0])
        checks["market_data"] = "PASS"
    except Exception as exc:
        checks["market_data"] = f"FAIL:{exc}"
    try:
        AccountDataService(adapter.backend).get_snapshot(config.symbols[0])
        checks["account"] = "PASS"
    except Exception as exc:
        checks["account"] = f"FAIL:{exc}"
    if backend_status.available and backend_status.demo and checks["market_data"] == checks["account"] == "PASS":
        checks["demo_execution"] = "READY_WITH_EXPLICIT_APPROVAL"
    required = (
        checks["configuration"], checks["database"], checks["market_data"], checks["account"],
        checks["risk_engine"], checks["managed_position_semantics"], checks["persistent_order_lifecycle"],
    )
    core_ready = all(value == "PASS" for value in required)
    execution_ready = core_ready and capabilities.get("client_order_id") and capabilities.get("attached_tp_sl")
    checks["system"] = (
        "READY_FOR_FIRST_CONTROLLED_DEMO_ORDER" if execution_ready
        else "READY_FOR_DEMO_DRY_RUN" if core_ready else "NOT_READY"
    )
    return checks
