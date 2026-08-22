from __future__ import annotations

from execution.base_backend import BaseBackend
from execution.cli_backend import CLIBackend
from execution.mcp_backend import MCPBackend
from execution.native_api_backend import NativeAPIBackend


class OKXAdapter:
    def __init__(self, backend_name: str = "mcp", backend: BaseBackend | None = None) -> None:
        self.backend_name = backend_name.lower()
        self.backend = backend or self._select(self.backend_name)

    @staticmethod
    def _select(name: str) -> BaseBackend:
        if name == "mcp":
            return MCPBackend()
        if name == "cli":
            return CLIBackend()
        if name in {"native", "native_api"}:
            return NativeAPIBackend()
        raise ValueError(f"UNKNOWN_BACKEND:{name}")

    def close(self) -> None:
        self.backend.close()

    def __enter__(self) -> "OKXAdapter":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
