from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]


class ConfigurationError(RuntimeError):
    """Raised when required configuration is missing, invalid, or disabled."""


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigurationError(f"CONFIG_MISSING:{path}")
    with path.open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle)
    if not isinstance(value, dict):
        raise ConfigurationError(f"CONFIG_INVALID:{path}")
    return value


@dataclass(frozen=True)
class AppConfig:
    rules: dict[str, Any]
    symbols: tuple[str, ...]
    environments: dict[str, Any]
    root: Path = ROOT

    @property
    def environment(self) -> str:
        return str(self.rules["environment"]).lower()

    @property
    def backend(self) -> str:
        return str(self.rules["execution"]["backend"]).lower()


def load_config(root: Path | None = None) -> AppConfig:
    base = root or ROOT
    rules = _load_yaml(base / "config" / "trading_rules.yaml")
    symbols_doc = _load_yaml(base / "config" / "symbols.yaml")
    environments = _load_yaml(base / "config" / "environments.yaml")
    symbols = tuple(str(item).upper() for item in symbols_doc.get("symbols", []))
    required = {"market", "timeframes", "risk", "trade", "scalping", "execution", "approval"}
    missing = required.difference(rules)
    if missing or not symbols:
        raise ConfigurationError(f"CONFIG_INVALID:missing={sorted(missing)} symbols={len(symbols)}")
    environment = str(rules.get("environment", "")).lower()
    env_doc = environments.get("environments", {}).get(environment)
    if not isinstance(env_doc, dict) or not env_doc.get("enabled", False):
        raise ConfigurationError(f"ENVIRONMENT_DISABLED:{environment}")
    return AppConfig(rules=rules, symbols=symbols, environments=environments, root=base)
