from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Metrics:
    counters: dict[str, int] = field(default_factory=dict)

    def increment(self, name: str) -> None:
        self.counters[name] = self.counters.get(name, 0) + 1
