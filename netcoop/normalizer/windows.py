"""Per-interval counter deltas. None and non-finite values remain gaps rather than becoming zero."""

from __future__ import annotations

import math
from collections.abc import Iterable


def _number(value: int | float | None) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def counter_deltas(values: Iterable[int | float | None]) -> tuple[list[float | None], list[int]]:
    """Return interval deltas and zero-based indices of reset samples.

    Delta i compares input samples i and i+1. Missing samples or a negative
    delta produce None for that interval. A reset sample becomes the baseline
    for the following interval; its negative jump is never counted as errors.
    """
    samples = [_number(value) for value in values]
    deltas: list[float | None] = []
    reset_indices: list[int] = []
    for index in range(1, len(samples)):
        previous, current = samples[index - 1], samples[index]
        if previous is None or current is None:
            deltas.append(None)
            continue
        delta = current - previous
        if delta < 0:
            deltas.append(None)
            reset_indices.append(index)
        else:
            deltas.append(delta)
    return deltas, reset_indices
