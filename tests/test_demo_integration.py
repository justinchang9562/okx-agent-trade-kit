from dataclasses import replace

import pytest

from execution.mcp_backend import MCPBackend, discover_demo_mcp_command
from execution.okx_adapter import OKXAdapter
from trading_agent.config import load_config
from trading_agent.orchestrator import TradingOrchestrator


@pytest.mark.integration
def test_real_demo_readonly_pipeline(tmp_path) -> None:
    if not discover_demo_mcp_command():
        pytest.skip("OKX Demo MCP wrapper is not configured")
    config = replace(load_config(), root=tmp_path)
    backend = MCPBackend()
    status = backend.status()
    assert status.available and status.demo
    # This is the full production pipeline through trade-plan construction.
    # TradingOrchestrator has no implicit call to OrderManager, so no order is submitted.
    with TradingOrchestrator(config, OKXAdapter("mcp", backend)) as orchestrator:
        plan = orchestrator.analyze("BTC-USDT", persist=False)
    assert plan.current_price > 0
    assert plan.decision in {"BUY", "HOLD", "REJECT"}
    assert plan.backend == "mcp" and plan.environment == "demo"
