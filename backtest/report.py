from __future__ import annotations

from typing import Any


def markdown_report(result: dict[str, Any]) -> str:
    performance = result["performance"]
    lines = [f"# Backtest — {result['symbol']}", "", f"Source: {result['source']}", ""]
    lines.extend(f"- {key}: {value}" for key, value in performance.items())
    return "\n".join(lines) + "\n"
