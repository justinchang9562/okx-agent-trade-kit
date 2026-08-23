from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

SENSITIVE = {"api_key", "apikey", "secret", "passphrase", "token", "authorization"}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if any(token in key.lower() for token in SENSITIVE) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def configure_logging(path: Path) -> logging.Logger:
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("trading_agent")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger


def log_event(logger: logging.Logger, event: str, payload: dict[str, Any]) -> None:
    logger.info(json.dumps({"event": event, "payload": _redact(payload)}, default=str, separators=(",", ":")))
