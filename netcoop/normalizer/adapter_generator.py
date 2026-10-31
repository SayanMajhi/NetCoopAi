"""Read and flatten untouched organizer-generator JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from netcoop.normalizer.normalize import RAW_METRICS



def load_raw(path: str | Path) -> list[dict[str, Any]]:
    """Return one intermediate dictionary for each metric in a raw JSON file."""
    raw_path = Path(path)

    try:
        document = json.loads(raw_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{raw_path}: invalid JSON: {exc.msg}") from exc

    if isinstance(document, dict) and "records" in document:
        records = document["records"]
    elif isinstance(document, dict):
        records = [document]
    elif isinstance(document, list) and document:
        records = document
    else:
        raise ValueError(f"{raw_path}: expected a JSON object or nonempty list")
    if not isinstance(records, list) or not records:
        raise ValueError(f"{raw_path}: expected nonempty records")

    observations: list[dict[str, Any]] = []

    for number, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ValueError(f"{raw_path}: record {number} must be an object")

        unknown = set(record) - {"timestamp", "device_id", "link_id", "source_id", "metrics", "status"}
        if unknown:
            raise ValueError(
                f"{raw_path}: record {number} has unmapped fields: {sorted(unknown)}"
            )

        timestamp = record.get("timestamp")
        device_id = record.get("device_id")
        link_id = record.get("link_id")
        metrics = record.get("metrics")

        if not isinstance(timestamp, str) or not timestamp:
            raise ValueError(f"{raw_path}: record {number} needs a timestamp")
        if bool(device_id) == bool(link_id) or not isinstance(device_id or link_id, str):
            raise ValueError(f"{raw_path}: record {number} needs one device_id or link_id")
        if not isinstance(metrics, dict) or not metrics:
            raise ValueError(f"{raw_path}: record {number} needs metrics")
        source_id = record.get("source_id", "organizer-generator")
        if not isinstance(source_id, str) or not source_id:
            raise ValueError(f"{raw_path}: record {number} needs a source_id")

        for raw_name, original_value in metrics.items():
            if raw_name not in RAW_METRICS:
                raise ValueError(
                    f"{raw_path}: record {number} has unmapped metric {raw_name!r}"
                )

            metric, unit, multiplier = RAW_METRICS[raw_name]
            observations.append(
                {
                    "source_id": source_id,
                    "observed_at": timestamp,
                    **({"device_id": device_id} if device_id else {"link_id": link_id}),
                    "metric": metric,
                    "value": original_value * multiplier if isinstance(original_value, (int, float)) and not isinstance(original_value, bool) else original_value,
                    "unit": unit,
                    "original_field": f"metrics.{raw_name}",
                    "original_value": original_value,
                    "normalization_steps": ([f"{raw_name} -> {metric}"] if raw_name != metric else [])
                    + ([f"{raw_name} x {multiplier:g} -> {unit}"] if multiplier != 1 else []),
                }
            )

    return observations
