import pytest

from execution.cli_backend import CLIBackend
from execution.mcp_backend import MCPBackend
from execution.native_api_backend import NativeAPIBackend
from execution.okx_adapter import OKXAdapter


def test_backend_selection() -> None:
    assert isinstance(OKXAdapter("mcp").backend, MCPBackend)
    assert isinstance(OKXAdapter("cli").backend, CLIBackend)
    assert isinstance(OKXAdapter("native_api").backend, NativeAPIBackend)


def test_unknown_backend_fails_closed() -> None:
    with pytest.raises(ValueError, match="UNKNOWN_BACKEND"):
        OKXAdapter("imaginary")


def test_native_reports_not_configured() -> None:
    status = NativeAPIBackend().status()
    assert not status.available and status.state == "NOT_CONFIGURED"
