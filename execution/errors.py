from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"(?i)(api[_-]?key|apikey|secret(?:[_-]?key)?|passphrase|authorization|"
    r"ok-access-(?:key|sign|passphrase)|token|password|signature)"
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(api[_-]?key|apikey|secret(?:[_-]?key)?|passphrase|authorization|"
    r"ok-access-(?:key|sign|passphrase)|token|password|signature)"
    r"(\s*[\"']?\s*[:=]\s*[\"']?|\s+)([^\s,;}\]]+)"
)
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")


def sanitize_diagnostic_value(value: Any) -> Any:
    """Recursively redact credential-shaped keys and labelled values."""
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _SENSITIVE_KEY.search(str(key)) else sanitize_diagnostic_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_diagnostic_value(item) for item in value]
    if isinstance(value, str):
        redacted = _BEARER.sub(r"\1[REDACTED]", value)
        return _SENSITIVE_ASSIGNMENT.sub(r"\1\2[REDACTED]", redacted)
    return value


class ExecutionError(RuntimeError):
    """Base class for classified execution failures."""


class PreSubmitRejectedError(ExecutionError):
    """A local or explicit exchange rejection known to precede acceptance."""

    def __init__(
        self,
        reason: str,
        *,
        diagnostics: dict[str, Any] | None = None,
        display_message: str | None = None,
    ) -> None:
        self.reason = str(sanitize_diagnostic_value(reason))
        sanitized = sanitize_diagnostic_value(diagnostics or {})
        self.diagnostics = sanitized if isinstance(sanitized, dict) else {}
        super().__init__(str(sanitize_diagnostic_value(display_message or self.reason)))


class SubmissionUncertainError(ExecutionError):
    """The request may have reached OKX and must be reconciled before retry."""


class StateChangedError(ExecutionError):
    """Optimistic state transition lost a compare-and-swap race."""
