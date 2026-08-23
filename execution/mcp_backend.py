from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from execution.base_backend import BackendStatus, BaseBackend
from execution.errors import PreSubmitRejectedError, SubmissionUncertainError


class MCPError(RuntimeError):
    """Raised for fail-closed transport or upstream MCP tool failures."""


class StdioMCPClient:
    def __init__(self, command: str | Sequence[str], timeout: float = 15.0) -> None:
        self.command = command
        self.timeout = timeout
        self._next_id = 1
        self._request_lock = threading.Lock()
        self._stderr_lines = 0
        self._process = subprocess.Popen(
            [command] if isinstance(command, str) else list(command),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()
        try:
            self._request("initialize", {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "okx-agent-trade-kit", "version": "0.3.0"},
            })
            self._notify("notifications/initialized", {})
        except Exception:
            self.close()
            raise

    def _drain_stderr(self) -> None:
        """Continuously drain stderr without retaining or emitting possibly sensitive content."""
        stream = self._process.stderr
        if stream is None:
            return
        for _line in stream:
            self._stderr_lines += 1

    def _write(self, message: dict[str, Any]) -> None:
        if not self._process.stdin:
            raise MCPError("MCP_STDIN_UNAVAILABLE")
        self._process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self._process.stdin.flush()

    def _read_response(self, request_id: int) -> dict[str, Any]:
        if not self._process.stdout:
            raise MCPError("MCP_STDOUT_UNAVAILABLE")
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise MCPError(f"MCP_EXITED:{self._process.returncode}:STDERR_LINES={self._stderr_lines}")
            ready, _, _ = select.select([self._process.stdout], [], [], min(0.25, deadline - time.monotonic()))
            if not ready:
                continue
            line = self._process.stdout.readline()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"] if isinstance(message["error"], dict) else {}
                raise MCPError(f"MCP_ERROR:{error.get('code', 'UNKNOWN')}")
            result = message.get("result")
            if not isinstance(result, dict):
                raise MCPError("MCP_INVALID_RESPONSE")
            return result
        raise MCPError("MCP_TIMEOUT")

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self._request_lock.acquire(timeout=self.timeout):
            raise MCPError("MCP_CONCURRENT_REQUEST_TIMEOUT")
        try:
            request_id = self._next_id
            self._next_id += 1
            self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            return self._read_response(request_id)
        finally:
            self._request_lock.release()

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        if not self._request_lock.acquire(timeout=self.timeout):
            raise MCPError("MCP_CONCURRENT_REQUEST_TIMEOUT")
        try:
            self._write({"jsonrpc": "2.0", "method": method, "params": params})
        finally:
            self._request_lock.release()

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise MCPError(f"MCP_TOOL_ERROR:{name}")
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            if structured.get("ok") is False:
                raise MCPError(f"MCP_TOOL_FAILED:{name}")
            return structured
        content = result.get("content", [])
        if content and isinstance(content[0], dict):
            try:
                parsed = json.loads(content[0].get("text", "{}"))
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                parsed = None
        raise MCPError(f"MCP_UNSTRUCTURED_RESPONSE:{name}")

    def list_tools(self) -> dict[str, Any]:
        return self._request("tools/list", {})

    def close(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
        if hasattr(self, "_stderr_thread"):
            self._stderr_thread.join(timeout=1)


def discover_demo_mcp_command() -> str | None:
    override = os.environ.get("OKX_MCP_COMMAND", "").strip()
    if override:
        candidate = Path(override).expanduser()
        return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None
    found = shutil.which("okx-mcp-demo-trade")
    if found:
        return found
    candidate = Path.home() / ".local" / "bin" / "okx-mcp-demo-trade"
    return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None


class MCPBackend(BaseBackend):
    name = "mcp"

    def __init__(
        self,
        command: str | None = None,
        client: StdioMCPClient | None = None,
        argv: Sequence[str] | None = None,
    ) -> None:
        self.command = command or discover_demo_mcp_command()
        self.argv = tuple(argv) if argv is not None else None
        self._client = client
        self._last_error = ""
        self._tool_cache: dict[str, dict[str, Any]] | None = None

    @property
    def client(self) -> StdioMCPClient:
        if self._client is None:
            if not self.command and not self.argv:
                raise MCPError("OKX_DEMO_MCP_NOT_CONFIGURED")
            self._client = StdioMCPClient(self.argv or str(self.command))
        return self._client

    def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.client.call_tool(tool, arguments)

    def _tools(self) -> dict[str, dict[str, Any]]:
        if self._tool_cache is None:
            self._tool_cache = {
                str(item.get("name")): item
                for item in self.client.list_tools().get("tools", [])
                if isinstance(item, dict) and item.get("name")
            }
        return self._tool_cache

    def _tool_properties(self, name: str) -> set[str]:
        return set(self._tools().get(name, {}).get("inputSchema", {}).get("properties", {}))

    def status(self) -> BackendStatus:
        if not self.command and not self.argv:
            return BackendStatus(self.name, "NOT_CONFIGURED", False, False, "OKX_DEMO_MCP_NOT_CONFIGURED")
        try:
            result = self._call("system_get_capabilities", {})
            capabilities = result.get("data", {}).get("capabilities", result.get("capabilities", {}))
            demo = bool(capabilities.get("demo", False))
            modules = capabilities.get("moduleAvailability", {})
            ready = demo and all(modules.get(name, {}).get("status") == "enabled" for name in ("market", "spot", "account"))
            return BackendStatus(self.name, "CONNECTED" if ready else "BLOCKED", ready, demo,
                                 "" if ready else "DEMO_OR_REQUIRED_MODULE_UNAVAILABLE")
        except Exception as exc:
            self._last_error = str(exc)
            return BackendStatus(self.name, "UNAVAILABLE", False, False, self._last_error)

    def get_ticker(self, symbol: str) -> dict[str, Any]:
        return self._call("market_get_ticker", {"instId": symbol, "demo": True})

    def get_candles(
        self, symbol: str, timeframe: str, limit: int = 100,
        after: str | None = None, before: str | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"instId": symbol, "bar": timeframe, "limit": limit, "demo": True}
        if after:
            args["after"] = after
        if before:
            args["before"] = before
        return self._call("market_get_candles", args)

    def get_orderbook(self, symbol: str, depth: int = 5) -> dict[str, Any]:
        return self._call("market_get_orderbook", {"instId": symbol, "sz": depth, "demo": True})

    def get_instrument(self, symbol: str) -> dict[str, Any]:
        return self._call("market_get_instruments", {"instType": "SPOT", "instId": symbol, "demo": True})

    def get_account(self) -> dict[str, Any]:
        return self._call("account_get_balance_all", {"accounts": "trading", "showValuation": False})

    def get_open_orders(self, symbol: str | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"status": "open", "limit": 100}
        if symbol:
            args["instId"] = symbol
        return self._call("spot_get_orders", args)

    def get_fills(
        self,
        symbol: str | None = None,
        *,
        after: str | None = None,
        before: str | None = None,
        order_id: str | None = None,
        begin_ms: int | None = None,
        end_ms: int | None = None,
        archive: bool = False,
        limit: int = 100,
    ) -> dict[str, Any]:
        properties = self._tool_properties("spot_get_fills")
        requested = {
            "after": after,
            "before": before,
            "ordId": order_id,
            "begin": begin_ms,
            "end": end_ms,
            "archive": True if archive else None,
        }
        unsupported = sorted(key for key, value in requested.items() if value is not None and key not in properties)
        if unsupported:
            raise NotImplementedError(f"FILL_QUERY_CAPABILITY_UNAVAILABLE:{','.join(unsupported)}")
        safe_limit = max(1, min(int(limit), 20 if archive else 100))
        args: dict[str, Any] = {"limit": safe_limit}
        if symbol:
            args["instId"] = symbol
        for key, value in requested.items():
            if value is not None:
                args[key] = str(value) if key in {"begin", "end"} else value
        return self._call("spot_get_fills", args)

    def get_order(self, symbol: str, order_id: str) -> dict[str, Any]:
        return self._call("spot_get_order", {"instId": symbol, "ordId": order_id})

    def get_order_by_client_id(self, symbol: str, client_order_id: str) -> dict[str, Any]:
        return self._call("spot_get_order", {"instId": symbol, "clOrdId": client_order_id})

    def get_protection_orders(self, symbol: str | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"ordType": "conditional", "limit": 100}
        if symbol:
            args["instId"] = symbol
        return self._call("spot_get_algo_orders", args)

    def capabilities(self) -> dict[str, Any]:
        try:
            tools = self._tools()
        except Exception:
            return super().capabilities()
        def properties(name: str) -> set[str]:
            return self._tool_properties(name)
        fill_properties = properties("spot_get_fills")
        return {
            "client_order_id": "clOrdId" in properties("spot_get_order"),
            "attached_tp_sl": {"tpTriggerPx", "slTriggerPx"}.issubset(properties("spot_place_order"))
                              and "spot_get_algo_orders" in tools,
            "historical_pagination": {"after", "before"}.issubset(properties("market_get_candles")),
            "algo_order_query": "spot_get_algo_orders" in tools,
            "fills_pagination": {"after", "before"}.issubset(fill_properties),
            "fill_order_lookup": "ordId" in fill_properties,
            "fills_time_window": {"begin", "end"}.issubset(fill_properties),
            "fills_archive": "archive" in fill_properties,
            "concurrent_read_only": False,
        }

    def place_order(self, order: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._call("spot_place_order", order)
        except MCPError as exc:
            if str(exc).startswith(("MCP_TOOL_ERROR", "MCP_TOOL_FAILED", "MCP_ERROR")):
                raise PreSubmitRejectedError("EXCHANGE_EXPLICIT_REJECTION") from exc
            if str(exc).startswith(("MCP_CONCURRENT_REQUEST_TIMEOUT", "OKX_DEMO_MCP_NOT_CONFIGURED")):
                raise PreSubmitRejectedError("LOCAL_TRANSPORT_NOT_STARTED") from exc
            raise SubmissionUncertainError(str(exc)) from exc

    def cancel_order(self, symbol: str, order_id: str) -> dict[str, Any]:
        return self._call("spot_cancel_order", {"instId": symbol, "ordId": order_id})

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
            self._tool_cache = None


def public_read_only_mcp_backend() -> MCPBackend:
    """Create a separate no-credential MCP process exposing public market reads only."""
    executable = shutil.which("okx-trade-mcp")
    if not executable:
        raise MCPError("OKX_READ_ONLY_MCP_NOT_CONFIGURED")
    return MCPBackend(
        argv=(
            executable,
            "--demo",
            "--read-only",
            "--modules",
            "market",
        )
    )
