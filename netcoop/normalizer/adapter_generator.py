"""Read and flatten untouched organizer-generator JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# Raw metric name -> (canonical metric name, canonical unit).
_METRICS: dict[str, tuple[str, str]] = {
    "cpu_utilization_pct": ("cpu_pct", "percent"),
    "link_utilization_pct": ("link_utilization_pct", "percent"),
    "packet_loss_pct": ("packet_loss_pct", "percent"),
    "latency_ms": ("latency_ms", "ms"),
    "jitter_ms": ("jitter_ms", "ms"),
    "queue_drops": ("queue_drops", "count"),
    "interface_errors": ("interface_errors", "count"),
}


def load_raw(path: str | Path) -> list[dict[str, Any]]:
    """Return one intermediate dictionary for each metric in a raw JSON file."""
    raw_path = Path(path)

    try:
        document = json.loads(raw_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{raw_path}: invalid JSON: {exc.msg}") from exc

    if isinstance(document, dict):
        records = [document]
    elif isinstance(document, list) and document:
        records = document
    else:
        raise ValueError(f"{raw_path}: expected a JSON object or nonempty list")

    observations: list[dict[str, Any]] = []

    for number, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ValueError(f"{raw_path}: record {number} must be an object")

        unknown = set(record) - {"timestamp", "device_id", "metrics", "status"}
        if unknown:
            raise ValueError(
                f"{raw_path}: record {number} has unmapped fields: {sorted(unknown)}"
            )

        timestamp = record.get("timestamp")
        device_id = record.get("device_id")
        metrics = record.get("metrics")

        if not isinstance(timestamp, str) or not timestamp:
            raise ValueError(f"{raw_path}: record {number} needs a timestamp")
        if not isinstance(device_id, str) or not device_id:
            raise ValueError(f"{raw_path}: record {number} needs a device_id")
        if not isinstance(metrics, dict) or not metrics:
            raise ValueError(f"{raw_path}: record {number} needs metrics")

        for raw_name, original_value in metrics.items():
            if raw_name not in _METRICS:
                raise ValueError(
                    f"{raw_path}: record {number} has unmapped metric {raw_name!r}"
                )

            metric, unit = _METRICS[raw_name]
            observations.append(
                {
                    "source_id": "organizer-generator",
                    "observed_at": timestamp,
                    "device_id": device_id,
                    "metric": metric,
                    "value": original_value,
                    "unit": unit,
                    "original_field": f"metrics.{raw_name}",
                    "original_value": original_value,
                }
            )

    return observations