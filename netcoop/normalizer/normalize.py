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
    "memory_utilization_pct": ("memory_pct", "percent", 1),
    "link_utilization_pct": ("link_utilization_pct", "percent", 1),
    "packet_loss_pct": ("packet_loss_pct", "percent", 1),
    "latency_ms": ("latency_ms", "ms", 1),
    "ping_ms": ("latency_ms", "ms", 1),
    "rtt_ms": ("latency_ms", "ms", 1),
    "rtt_sec": ("latency_ms", "ms", 1000),
    "rtt_s": ("latency_ms", "ms", 1000),
    "latency_s": ("latency_ms", "ms", 1000),
    "latency_sec": ("latency_ms", "ms", 1000),
    "jitter_ms": ("jitter_ms", "ms", 1),
    "jitter_s": ("jitter_ms", "ms", 1000),
    "jitter_sec": ("jitter_ms", "ms", 1000),
    "throughput_mbps": ("throughput_mbps", "Mbps", 1),
    "throughput_kbps": ("throughput_mbps", "Mbps", 0.001),
    "capacity_mbps": ("capacity_mbps", "Mbps", 1),
    "queue_drops": ("queue_drops", "count", 1),
    "interface_errors": ("interface_errors", "count", 1),
    "crc_errors": ("interface_errors", "count", 1),
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
COUNTERS = {"queue_drops", "interface_errors", "link_flap_count"}   # cumulative: judged per interval
_COUNTERS = COUNTERS
_PERCENTAGES = {"cpu_pct", "memory_pct", "link_utilization_pct", "packet_loss_pct", "retry_rate_pct"}
_WRAPPER_KEYS = {"incident_id", "window_end", "complaint", "records", "messages", "inject", "on_query"}
ON_DEMAND_PREFIX = "on-demand"   # source_id of probe samples that are run only when a QUERY asks
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
    """Normalize the window's records; an optional `on_query` block holds on-demand probe samples
    (source_id starting "on-demand") that agents release only when Correlation QUERYs them."""
    probes = fixture.get("on_query") if isinstance(fixture, dict) else None
    main = {k: v for k, v in fixture.items() if k != "on_query"} if probes is not None else fixture
    result = _normalize_window(main, topology, signatures)
    if probes is None or result.validation_errors:
        return result
    if not isinstance(probes, list) or not probes or any(
            not isinstance(r, dict) or not str(r.get("source_id", "")).startswith(ON_DEMAND_PREFIX) for r in probes):
        result.validation_errors.append(f"on_query: expected a non-empty list of records with source_id '{ON_DEMAND_PREFIX}-…'")
        result.observations.clear()
        return result
    probe = _normalize_window({"window_end": main.get("window_end"), "records": probes}, topology, signatures)
    if probe.validation_errors:
        result.validation_errors += [f"on_query: {e}" for e in probe.validation_errors]
        result.observations.clear()
        return result
    start = len(result.observations)
    result.on_demand = [o.model_copy(update={"evidence_id": f"EV-{start + i:03d}"})
                        for i, o in enumerate(probe.observations, 1)]
    result.normalization_log += [f"on_query {line}" for line in probe.normalization_log]
    result.skipped += [_prefixed(entry, "on_query: ") for entry in probe.skipped]
    result.suspect += [f"on_query: {entry}" for entry in probe.suspect]
    return result


def _prefixed(entry: str, prefix: str) -> str:
    """Insert `prefix` after the error code of a typed skip entry ("CODE detail")."""
    code, _, detail = entry.partition(" ")
    return f"{code} {prefix}{detail}"


def _normalize_window(fixture: dict, topology: dict, signatures: dict) -> NormalizationResult:
    """Return evidence or field-level errors; never raise for bad fixture data.

    One bad field or record does not sink the incident: an unknown metric or key, or a record
    without a usable timestamp, target, source or metrics, is skipped and named in `skipped`
    (typed MISSING_FIELD / INVALID_VALUE). Only a broken wrapper, or nothing usable left, is fatal."""
    document, errors = _load(fixture)
    result = NormalizationResult(validation_errors=errors)
    if errors:
        return result
    if isinstance(document, dict) and "records" in document:
        unknown = set(document) - _WRAPPER_KEYS
        records = document.get("records")
        if unknown:
            result.skipped.append(f"INVALID_VALUE fixture: unknown fields {sorted(unknown)}; ignored")
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
    skip = result.skipped.append
    for number, record in enumerate(records, 1):
        prefix = f"records[{number - 1}]"
        if not isinstance(record, dict):
            skip(f"INVALID_VALUE {prefix}: expected an object; record skipped")
            continue
        unknown = set(record) - _RECORD_KEYS
        if unknown:
            skip(f"INVALID_VALUE {prefix}: unknown fields {sorted(unknown)}; ignored, rest of the record kept")
        observed_at = _timestamp(record.get("timestamp"))
        if observed_at is None:
            code = "MISSING_FIELD" if record.get("timestamp") is None else "INVALID_VALUE"
            skip(f"{code} {prefix}.timestamp: required timezone-aware timestamp; record skipped")
            continue
        raw_ts = record.get("timestamp")
        if not raw_ts.endswith(("Z", "+00:00")):      # C5: non-UTC offsets are converted, and the log says so
            result.normalization_log.append(f"{prefix}.timestamp {raw_ts} converted to UTC {observed_at.isoformat()}")
        device_id, link_id = record.get("device_id"), record.get("link_id")
        if bool(device_id) == bool(link_id) or not isinstance(device_id or link_id, str):
            code = "MISSING_FIELD" if not device_id and not link_id else "INVALID_VALUE"
            skip(f"{code} {prefix}: exactly one device_id or link_id is required; record skipped")
            continue
        target = device_id or link_id
        if target not in (node_ids if device_id else link_by_id):
            skip(f"INVALID_VALUE {prefix}: unknown topology target {target!r}; record skipped")
            continue
        source = record.get("source_id", "organizer-generator")
        if not isinstance(source, str) or not source:
            skip(f"INVALID_VALUE {prefix}.source_id: expected a nonempty string; record skipped")
            continue
        metrics = record.get("metrics")
        if not isinstance(metrics, dict) or not metrics:
            code = "MISSING_FIELD" if metrics is None else "INVALID_VALUE"
            skip(f"{code} {prefix}.metrics: expected a nonempty object; record skipped")
            continue
        for raw_name, raw in metrics.items():
            if raw_name not in RAW_METRICS:
                skip(f"INVALID_VALUE {prefix}.metrics.{raw_name}: unknown raw metric; skipped")
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
    if result.window_end is None and rows:
        result.window_end = max(row["observed_at"] for row in rows)
    if not rows and not result.validation_errors:
        result.validation_errors.append("records: no usable telemetry left after skipping the items above")
    if result.validation_errors:              # fatal: name every skipped item too, first
        result.validation_errors[:0] = result.skipped
        result.skipped = []
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
    _utilization(rows, link_by_id, result.normalization_log, signatures)
    _counter_resets(rows, result.normalization_log)
    _fraction_utilization(rows, result)
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


def _utilization(rows: list[dict[str, Any]], links: dict[str, dict], log: list[str],
                 signatures: dict[str, Any]) -> None:
    check = signatures.get("utilization_cross_check") or {}
    tolerance = check.get("tolerance_points", 5)
    max_ratio = check.get("max_throughput_ratio_pct", 110)
    util_th = (signatures.get("thresholds") or {}).get("link_utilization_pct") or {}
    warn, critical = util_th.get("warn"), util_th.get("critical")
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
            m, when = measured["value"], f"{target} at {observed_at.isoformat()}"
            if derived > max_ratio:
                log.append(f"{when}: throughput/capacity {derived:g}% is impossible (> {max_ratio:g}%); "
                           f"cross-check skipped, measured utilization {m:g}% kept")
            elif abs(m - derived) > tolerance:
                verdicts_differ = warn is not None and critical is not None and (
                    (m > critical and derived <= warn) or (derived > critical and m <= warn))
                note = f"cross-check mismatch: measured {m:g}% vs throughput/capacity {derived:g}%"
                measured["normalization_steps"] = measured["normalization_steps"] + [note]
                if verdicts_differ:
                    measured["quality_flags"] = [f for f in measured["quality_flags"] if f != "ok"] + ["malformed"]
                    log.append(f"{when}: {note}; they disagree about the verdict (critical {critical:g}% vs "
                               f"warn {warn:g}%), measured value marked malformed")
                else:
                    log.append(f"{when}: {note} (over {tolerance:g} points) but both give the same verdict; "
                               "measured value kept")
            if derived > max_ratio:
                continue
            # Keep the second, independent estimate too (flagged `derived`): Correlation judges each
            # estimator's series on its own, so one noisy counter cannot hide what the other shows.
        flags = ["derived"]
        steps = [f"throughput_mbps / capacity_mbps ({capacity:g}) x 100"]
        if "stale" in throughput["quality_flags"]:
            flags += ["stale", "out_of_window"]
        if math.isfinite(derived) and 100 < derived <= max_ratio:
            steps.append(f"{derived:g}% capped at 100% (counter timing jitter within {max_ratio:g}%)")
            derived = 100.0
        if not math.isfinite(derived) or derived < 0 or derived > 100:
            flags.append("malformed")
            log.append(f"{target} at {observed_at.isoformat()}: derived utilization outside 0-100%; check capacity and units")
        rows.append({
            "source_id": throughput["source_id"], "observed_at": observed_at,
            "device_id": throughput["device_id"], "link_id": throughput["link_id"],
            "metric": "link_utilization_pct", "value": derived if "malformed" not in flags else None, "unit": "percent",
            "original_field": throughput["original_field"], "original_value": throughput["original_value"],
            "normalization_steps": steps,
            "quality_flags": flags, "_target": target, "_ordinal": len(rows),
        })
        log.append(f"{target} at {observed_at.isoformat()}: derived utilization {derived:g}% from throughput/capacity")


def _fraction_utilization(rows: list[dict[str, Any]], result: NormalizationResult) -> None:
    """A 0-1 fraction sent as percent reads as an idle link. The scale is not guessed or converted,
    but a link whose every measured sample lies in (0, 1] is named in `suspect`."""
    series: dict[str, list[float]] = {}
    for row in rows:
        if row["metric"] == "link_utilization_pct" and "derived" not in row["quality_flags"] and row["value"] is not None:
            series.setdefault(row["_target"], []).append(row["value"])
    for target, values in sorted(series.items()):
        if len(values) >= 2 and all(0 < v <= 1 for v in values):
            note = (f"{target} link_utilization_pct: all {len(values)} samples lie between 0 and 1, so they may be "
                    "a 0-1 fraction rather than percent; kept as percent, not converted")
            result.suspect.append(note)
            result.normalization_log.append(note)


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
