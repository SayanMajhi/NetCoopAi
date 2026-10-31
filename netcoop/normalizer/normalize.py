"""Convert scenario or organizer telemetry to deterministic canonical evidence."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from netcoop.normalizer.result import NormalizationResult
from netcoop.normalizer.windows import counter_deltas
from netcoop.protocol.models import Observation


# Raw name -> canonical name, unit, multiplier. A multiplier is only a unit conversion.
RAW_METRICS: dict[str, tuple[str, str, float]] = {
    "cpu_utilization_pct": ("cpu_pct", "percent", 1),
    "cpu_pct": ("cpu_pct", "percent", 1),
    "memory_pct": ("memory_pct", "percent", 1),
    "link_utilization_pct": ("link_utilization_pct", "percent", 1),
    "packet_loss_pct": ("packet_loss_pct", "percent", 1),
    "latency_ms": ("latency_ms", "ms", 1),
    "rtt_sec": ("latency_ms", "ms", 1000),
    "jitter_ms": ("jitter_ms", "ms", 1),
    "throughput_mbps": ("throughput_mbps", "Mbps", 1),
    "throughput_kbps": ("throughput_mbps", "Mbps", 0.001),
    "capacity_mbps": ("capacity_mbps", "Mbps", 1),
    "queue_drops": ("queue_drops", "count", 1),
    "interface_errors": ("interface_errors", "count", 1),
    "link_flap_count": ("link_flap_count", "count", 1),
    "interface_oper_status": ("interface_oper_status", "state", 1),
    "rssi_dbm": ("rssi_dbm", "dBm", 1),
    "retry_rate_pct": ("retry_rate_pct", "percent", 1),
    "retries": ("retry_rate_pct", "percent", 1),
    "retransmissions": ("retry_rate_pct", "percent", 1),
    "dns_success": ("dns_success", "boolean", 1),
    "dhcp_success": ("dhcp_success", "boolean", 1),
    "reachability": ("reachability", "boolean", 1),
}
_BOOLEANS = {"dns_success", "dhcp_success", "reachability"}
_COUNTERS = {"queue_drops", "interface_errors", "link_flap_count"}
_PERCENTAGES = {"cpu_pct", "memory_pct", "link_utilization_pct", "packet_loss_pct", "retry_rate_pct"}
_WRAPPER_KEYS = {"incident_id", "window_end", "complaint", "records", "messages", "inject"}
_RECORD_KEYS = {"timestamp", "device_id", "link_id", "source_id", "metrics", "status"}


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def _value(raw: Any, metric: str, multiplier: float) -> tuple[Any, list[str]]:
    if raw is None:
        return None, ["missing"]
    if metric in _BOOLEANS:
        return (raw, []) if isinstance(raw, bool) else (None, ["malformed"])
    if metric == "interface_oper_status":
        return (raw.lower(), []) if isinstance(raw, str) and raw.lower() in {"up", "down", "flapping"} else (None, ["malformed"])
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None, ["malformed"]
    try:
        value = raw * multiplier
        if not math.isfinite(value):
            return None, ["malformed"]
    except (OverflowError, ValueError):
        return None, ["malformed"]
    if (metric in _PERCENTAGES and not 0 <= value <= 100) or (metric in _COUNTERS and value < 0) or (metric == "capacity_mbps" and value <= 0):
        return None, ["malformed"]
    return value, []


def _load(fixture: Any) -> tuple[Any, list[str]]:
    if not isinstance(fixture, (str, Path)):
        return fixture, []
    try:
        return json.loads(Path(fixture).read_text(encoding="utf-8")), []
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, [f"fixture: unreadable or invalid JSON: {exc}"]


def normalize(fixture: dict, topology: dict, signatures: dict) -> NormalizationResult:
    """Return evidence or field-level errors; never raise for bad fixture data."""
    document, errors = _load(fixture)
    result = NormalizationResult(validation_errors=errors)
    if errors:
        return result
    if isinstance(document, dict) and "records" in document:
        unknown = set(document) - _WRAPPER_KEYS
        records = document.get("records")
        if unknown:
            result.validation_errors.append(f"fixture: unknown fields {sorted(unknown)}")
        result.window_end = _timestamp(document.get("window_end"))
        if result.window_end is None:
            result.validation_errors.append("window_end: required timezone-aware timestamp")
    elif isinstance(document, dict):
        records = [document]
    else:
        result.validation_errors.append("fixture: expected an object")
        return result
    if not isinstance(records, list) or not records:
        result.validation_errors.append("records: expected a nonempty list")
        return result
    if not isinstance(topology, dict) or not isinstance(signatures, dict):
        result.validation_errors.append("topology and signatures must be objects")
        return result
    nodes = topology.get("nodes", [])
    links = topology.get("links", [])
    if not isinstance(nodes, list) or not isinstance(links, list):
        result.validation_errors.append("topology: nodes and links must be lists")
        return result
    if any(not isinstance(n, dict) or not isinstance(n.get("id"), str) for n in nodes) or any(
        not isinstance(link, dict) or not isinstance(link.get("link_id"), str) for link in links
    ):
        result.validation_errors.append("topology: each node needs a string id and each link a string link_id")
        return result
    node_ids = {n.get("id") for n in nodes if isinstance(n, dict)}
    link_by_id = {link.get("link_id"): link for link in links if isinstance(link, dict)}
    rows: list[dict[str, Any]] = []
    timestamps: list[datetime] = []
    for number, record in enumerate(records, 1):
        prefix = f"records[{number - 1}]"
        if not isinstance(record, dict):
            result.validation_errors.append(f"{prefix}: expected an object")
            continue
        unknown = set(record) - _RECORD_KEYS
        if unknown:
            result.validation_errors.append(f"{prefix}: unknown fields {sorted(unknown)}")
        observed_at = _timestamp(record.get("timestamp"))
        if observed_at is None:
            result.validation_errors.append(f"{prefix}.timestamp: required timezone-aware timestamp")
            continue
        timestamps.append(observed_at)
        device_id, link_id = record.get("device_id"), record.get("link_id")
        if bool(device_id) == bool(link_id) or not isinstance(device_id or link_id, str):
            result.validation_errors.append(f"{prefix}: exactly one device_id or link_id is required")
            continue
        target = device_id or link_id
        if target not in (node_ids if device_id else link_by_id):
            result.validation_errors.append(f"{prefix}: unknown topology target {target!r}")
            continue
        source = record.get("source_id", "organizer-generator")
        if not isinstance(source, str) or not source:
            result.validation_errors.append(f"{prefix}.source_id: expected a nonempty string")
            continue
        metrics = record.get("metrics")
        if not isinstance(metrics, dict) or not metrics:
            result.validation_errors.append(f"{prefix}.metrics: expected a nonempty object")
            continue
        for raw_name, raw in metrics.items():
            if raw_name not in RAW_METRICS:
                result.validation_errors.append(f"{prefix}.metrics.{raw_name}: unknown raw metric")
                continue
            metric, unit, multiplier = RAW_METRICS[raw_name]
            value, flags = _value(raw, metric, multiplier)
            steps: list[str] = []
            if raw_name != metric:
                flags.append("alias_mapped")
                steps.append(f"{raw_name} -> {metric}")
            if multiplier != 1 and value is not None:
                flags.append("unit_converted")
                steps.append(f"{raw_name} x {multiplier:g} -> {unit}")
            for step in steps:
                result.normalization_log.append(f"{prefix}.metrics.{raw_name}: {step}")
            rows.append({
                "source_id": source, "observed_at": observed_at,
                "device_id": device_id, "link_id": link_id,
                "metric": metric, "value": value, "unit": unit,
                "original_field": f"metrics.{raw_name}", "original_value": raw,
                "normalization_steps": steps, "quality_flags": flags or ["ok"],
                "_target": target, "_ordinal": len(rows),
            })
    if result.window_end is None and timestamps:
        result.window_end = max(timestamps)
    if result.validation_errors:
        return result
    assert result.window_end is not None
    minutes = signatures.get("window", {}).get("minutes", 5)
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or minutes <= 0:
        result.validation_errors.append("signatures.window.minutes: expected a positive number")
        return result
    start = result.window_end - timedelta(minutes=minutes)
    for row in rows:
        if not start <= row["observed_at"] <= result.window_end:
            row["quality_flags"] = [f for f in row["quality_flags"] if f != "ok"] + ["stale", "out_of_window"]
            result.normalization_log.append(f"{row['_target']} {row['metric']} at {row['observed_at'].isoformat()}: outside incident window")
    _utilization(rows, link_by_id, result.normalization_log)
    _counter_resets(rows, result.normalization_log)
    rows.sort(key=lambda r: (r["_target"], r["metric"], r["observed_at"], r["source_id"], r["original_field"], r["_ordinal"]))
    for index, row in enumerate(rows, 1):
        clean = {k: v for k, v in row.items() if not k.startswith("_")}
        try:
            result.observations.append(Observation.model_validate({"evidence_id": f"EV-{index:03d}", **clean}))
        except ValidationError as exc:
            result.validation_errors.append(f"observation {index}: {exc.errors()[0]['msg']}")
    if result.validation_errors:
        result.observations.clear()
    return result


def _utilization(rows: list[dict[str, Any]], links: dict[str, dict], log: list[str]) -> None:
    groups: dict[tuple[str, datetime], dict[str, dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row["_target"], row["observed_at"]), {})[row["metric"]] = row
    for (target, observed_at), metrics in groups.items():
        throughput = metrics.get("throughput_mbps")
        if throughput is None or throughput["value"] is None:
            continue
        capacity = metrics.get("capacity_mbps", {}).get("value")
        if capacity is None and target in links:
            capacity = links[target].get("capacity_mbps")
        if isinstance(capacity, bool) or not isinstance(capacity, (int, float)) or not math.isfinite(capacity) or capacity <= 0:
            log.append(f"{target} at {observed_at.isoformat()}: utilization unavailable without positive capacity")
            continue
        derived = 100 * throughput["value"] / capacity
        measured = metrics.get("link_utilization_pct")
        if measured is not None and measured["value"] is not None:
            if abs(measured["value"] - derived) > 5:
                measured["quality_flags"] = [f for f in measured["quality_flags"] if f != "ok"] + ["malformed"]
                log.append(f"{target} at {observed_at.isoformat()}: measured utilization {measured['value']:g}% differs from throughput/capacity {derived:g}% by over 5 points")
            continue
        flags = ["derived"]
        if "stale" in throughput["quality_flags"]:
            flags += ["stale", "out_of_window"]
        if not math.isfinite(derived) or derived < 0 or derived > 100:
            flags.append("malformed")
            log.append(f"{target} at {observed_at.isoformat()}: derived utilization outside 0-100%; check capacity and units")
        rows.append({
            "source_id": throughput["source_id"], "observed_at": observed_at,
            "device_id": throughput["device_id"], "link_id": throughput["link_id"],
            "metric": "link_utilization_pct", "value": derived if "malformed" not in flags else None, "unit": "percent",
            "original_field": throughput["original_field"], "original_value": throughput["original_value"],
            "normalization_steps": [f"throughput_mbps / capacity_mbps ({capacity:g}) x 100"],
            "quality_flags": flags, "_target": target, "_ordinal": len(rows),
        })
        log.append(f"{target} at {observed_at.isoformat()}: derived utilization {derived:g}% from throughput/capacity")


def _counter_resets(rows: list[dict[str, Any]], log: list[str]) -> None:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if row["metric"] in _COUNTERS:
            groups.setdefault((row["_target"], row["metric"], row["source_id"]), []).append(row)
    for group in groups.values():
        group.sort(key=lambda r: (r["observed_at"], r["_ordinal"]))
        values = [row["value"] if "stale" not in row["quality_flags"] else None for row in group]
        _, resets = counter_deltas(values)
        for index in resets:
            row = group[index]
            row["quality_flags"] = [flag for flag in row["quality_flags"] if flag != "ok"] + ["counter_reset"]
            log.append(f"{row['_target']} {row['metric']} at {row['observed_at'].isoformat()}: counter reset {values[index - 1]:g} -> {values[index]:g}")
