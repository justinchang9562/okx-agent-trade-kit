from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class WalkForwardWindow:
    index: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int


def windows(total_bars: int, train_bars: int, test_bars: int, step_bars: int) -> tuple[WalkForwardWindow, ...]:
    if min(total_bars, train_bars, test_bars, step_bars) <= 0:
        raise ValueError("INVALID_WALK_FORWARD_CONFIGURATION")
    output: list[WalkForwardWindow] = []
    test_start = train_bars
    index = 0
    while test_start + test_bars <= total_bars:
        output.append(WalkForwardWindow(
            index=index, train_start=test_start - train_bars, train_end=test_start,
            test_start=test_start, test_end=test_start + test_bars,
        ))
        index += 1
        test_start += step_bars
    return tuple(output)


def evaluate(data: tuple[T, ...], evaluator: Callable[[tuple[T, ...], tuple[T, ...]], dict],
             train_bars: int, test_bars: int, step_bars: int) -> list[dict]:
    """No optimizer: each result is explicitly out-of-sample for its rolling window."""
    return [
        {"window": window.__dict__, "out_of_sample": evaluator(
            data[window.train_start:window.train_end], data[window.test_start:window.test_end]
        )}
        for window in windows(len(data), train_bars, test_bars, step_bars)
    ]
