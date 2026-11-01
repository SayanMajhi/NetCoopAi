"""Judge-sim review (2026-11-01): aliased/second-based latency and one bad field or record must not
reject a whole incident; skipped or mis-scaled telemetry is named and blocks a `healthy` verdict."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from netcoop.normalizer.normalize import normalize
from netcoop.orchestrator.run import default_components, load_signatures, load_topology, run_incident

ROOT = Path(__file__).resolve().parents[1]
TOPOLOGY = load_topology()
SIGNATURES = load_signatures()
END = "2026-11-01T10:05:00Z"


def _dev(name: str) -> dict[str, Any]:
    return json.loads((ROOT / "scenarios" / "dev" / name).read_text(encoding="utf-8"))


def _run(fixture: dict[str, Any]):
    return run_incident(fixture, TOPOLOGY, SIGNATURES, components=default_components("local")).report


def _rename_metric(fixture: dict[str, Any], old: str, new: str, scale: float = 1.0) -> dict[str, Any]:
    fixture = copy.deepcopy(fixture)
    for record in fixture["records"]:
        if old in record["metrics"]:
            record["metrics"][new] = record["metrics"].pop(old) * scale
    return fixture


def test_ping_ms_and_latency_seconds_are_mapped_to_latency_ms() -> None:
    fixture = {"window_end": END, "records": [
        {"timestamp": "2026-11-01T10:04:00Z", "device_id": "SW_CAMPUS_3", "metrics": {"ping_ms": 240.0}},
        {"timestamp": "2026-11-01T10:04:00Z", "device_id": "SW_CAMPUS_8", "metrics": {"latency_s": 0.28}},
    ]}
    result = normalize(fixture, TOPOLOGY, SIGNATURES)
    assert result.validation_errors == [] and result.skipped == []
    by_device = {o.device_id: o for o in result.observations}
    assert by_device["SW_CAMPUS_3"].metric == "latency_ms" and by_device["SW_CAMPUS_3"].value == 240.0
    assert "alias_mapped" in by_device["SW_CAMPUS_3"].quality_flags
    assert by_device["SW_CAMPUS_8"].metric == "latency_ms" and round(by_device["SW_CAMPUS_8"].value, 6) == 280.0
    assert {"alias_mapped", "unit_converted"} <= set(by_device["SW_CAMPUS_8"].quality_flags)
    assert by_device["SW_CAMPUS_8"].original_value == 0.28


def test_one_bad_field_or_record_is_skipped_not_fatal() -> None:
    fixture = {"window_end": END, "records": [
        {"timestamp": "2026-11-01T10:04:00Z", "device_id": "SW_CAMPUS_1", "vendor": "aruba",
         "metrics": {"cpu_pct": 20.0, "mystery": 5}},
        {"device_id": "SW_CAMPUS_1", "metrics": {"cpu_pct": 21.0}},                          # no timestamp
        {"timestamp": "2026-11-01T10:04:00Z", "device_id": "SW_CAMPUS_99", "metrics": {"cpu_pct": 22.0}},
        {"timestamp": "2026-11-01T10:04:00Z", "device_id": "SW_CAMPUS_2"},                   # no metrics
    ]}
    result = normalize(fixture, TOPOLOGY, SIGNATURES)
    assert result.validation_errors == []
    assert [(o.device_id, o.value) for o in result.observations] == [("SW_CAMPUS_1", 20.0)]
    codes = [entry.split(" ")[0] for entry in result.skipped]
    assert codes == ["INVALID_VALUE", "INVALID_VALUE", "MISSING_FIELD", "INVALID_VALUE", "MISSING_FIELD"]
    assert any("records[0]: unknown fields ['vendor']" in e for e in result.skipped)
    assert any("records[0].metrics.mystery: unknown raw metric" in e for e in result.skipped)
    assert any("records[1].timestamp" in e for e in result.skipped)


def test_nothing_usable_left_is_still_invalid_input_and_names_every_item() -> None:
    fixture = {"window_end": END, "records": [
        {"timestamp": "2026-11-01T10:04:00Z", "device_id": "SW_CAMPUS_1", "metrics": {"mystery": 5}},
        {"device_id": "SW_CAMPUS_1", "metrics": {"cpu_pct": 21.0}},
    ]}
    report = _run(fixture)
    assert report.status == "invalid_input"
    assert "unknown raw metric" in report.validation_errors[0]
    assert "timestamp" in report.validation_errors[1]
    assert "no usable telemetry" in report.validation_errors[-1]


def test_congestion_with_aliased_fields_and_one_bad_record_is_still_diagnosed() -> None:
    fixture = _rename_metric(_dev("congestion.json"), "latency_ms", "ping_ms")
    fixture["records"][0]["vendor"] = "aruba"
    del fixture["records"][1]["timestamp"]
    report = _run(fixture)
    assert report.status == "diagnosed" and report.suspected_root_cause == "link_congestion@HOSTEL_UPLINK_01"
    assert any(e.startswith("INVALID_VALUE records[0]: unknown fields") for e in report.validation_errors)
    assert any(e.startswith("MISSING_FIELD records[1].timestamp") for e in report.validation_errors)
    assert any(lim.startswith("input: 2 telemetry item(s) skipped") for lim in report.limitations)


def test_skipped_telemetry_blocks_a_healthy_verdict() -> None:
    fixture = _dev("normal.json")
    assert _run(fixture).status == "healthy"                    # control: the clean input is healthy
    fixture["records"][0]["metrics"]["mystery_signal"] = 42
    report = _run(fixture)
    assert report.status != "healthy"
    assert any("skipped or look mis-scaled" in lim for lim in report.limitations)


def test_utilization_sent_as_a_fraction_is_named_and_blocks_healthy() -> None:
    fixture = copy.deepcopy(_dev("normal.json"))
    for record in fixture["records"]:
        if record.get("link_id") == "HOSTEL_AP_LINK_01":
            record["metrics"]["link_utilization_pct"] = round(record["metrics"]["link_utilization_pct"] / 100, 4)
    result = normalize(fixture, TOPOLOGY, SIGNATURES)
    assert len(result.suspect) == 1 and result.suspect[0].startswith("HOSTEL_AP_LINK_01 link_utilization_pct")
    report = _run(fixture)
    assert report.status != "healthy"
    assert any("0-1 fraction" in lim for lim in report.limitations)
