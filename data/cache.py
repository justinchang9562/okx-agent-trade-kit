from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class JsonCache:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, name: str, value: dict[str, Any]) -> Path:
        target = self.root / f"{name}.json"
        target.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
        return target

    def read(self, name: str) -> dict[str, Any] | None:
        target = self.root / f"{name}.json"
        return json.loads(target.read_text(encoding="utf-8")) if target.exists() else None
