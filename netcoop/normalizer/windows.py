"""Window statistics for canonical telemetry values.

Callers select the incident window and exclude stale or malformed observations.
None and non-finite values remain gaps rather than becoming zero.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from statistics import median as statistics_median


def _number(value: int | float | None) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def median(values: Iterable[int | float | None]) -> float | None:
    """Median of valid samples, or None when the window has no valid samples."""
    samples = [number for value in values if (number := _number(value)) is not None]
    return float(statistics_median(samples)) if samples else None


def p95(values: Iterable[int | float | None]) -> float | None:
    """Nearest-rank 95th percentile, or None for an empty valid window.

    With five samples, p95 is the highest sample. This avoids interpolating a
    value that was never observed in a short incident window.
    """
    samples = sorted(number for value in values if (number := _number(value)) is not None)
    if not samples:
        return None
    return samples[math.ceil(0.95 * len(samples)) - 1]


def sustained(values: Iterable[int | float | None], threshold: float,
              min_consecutive: int) -> bool:
    """True when enough adjacent valid samples are strictly above threshold."""
    if not isinstance(min_consecutive, int) or isinstance(min_consecutive, bool) or min_consecutive < 1:
        raise ValueError("min_consecutive must be a positive integer")
    if _number(threshold) is None:
        raise ValueError("threshold must be finite")
    streak = 0
    for value in values:
        number = _number(value)
        streak = streak + 1 if number is not None and number > threshold else 0
        if streak >= min_consecutive:
            return True
    return False


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
