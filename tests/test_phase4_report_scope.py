"""Phase 4 P4-1 (per-interval deltas next to cumulative counters) and P4-2 (healthy gate's
missing/stale rule scoped to the area in question). No threshold, weight or gate value changes."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from netcoop.agents.reporting import to_markdown
from netcoop.orchestrator.run import default_components, load_signatures, load_topology, run_incident
from netcoop.protocol.models import Report

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "scenarios" / "dev"
TOPOLOGY, SIGNATURES = load_topology(), load_signatures()


def fixture(name: str) -> dict:
    return json.loads((DEV / f"{name}.json").read_text(encoding="utf-8"))


def run(fx: dict) -> Report:
    return run_incident(fx, TOPOLOGY, SIGNATURES, components=default_components(connectivity="local")).report


# ---------------------------------------------------------------- P4-1


def test_counter_evidence_carries_the_per_interval_delta():
    report = run(fixture("interface_fault"))
    counters = [e for e in report.supporting_evidence if e.metric == "interface_errors"]
    assert counters and all(e.interval_delta is not None and e.interval_delta >= 0 for e in counters)
    assert all(e.interval_delta is None for e in report.supporting_evidence
               if e.metric not in ("interface_errors", "queue_drops", "link_flap_count"))
    md = to_markdown(report)
    assert "| per interval |" in md
    assert all(f"+{e.interval_delta:g}" in md for e in counters)


def test_delta_is_per_interval_and_none_on_first_sample_and_reset():
    from datetime import datetime, timezone
    from netcoop.agents.base import IncidentContext
    from netcoop.agents.reporting import _interval_delta
    from netcoop.normalizer.normalize import normalize
    fx = {"incident_id": "INC-T", "window_end": "2026-11-01T10:05:00Z", "records": [
        {"timestamp": f"2026-11-01T10:0{i}:00Z", "device_id": "SW_CAMPUS_7", "source_id": "switch-telemetry",
         "metrics": {"interface_errors": v}} for i, v in enumerate([100, 112, 130, 4, 9], 1)]}
    norm = normalize(fx, TOPOLOGY, SIGNATURES)
    ctx = IncidentContext("INC-T", datetime(2026, 11, 1, 10, 5, tzinfo=timezone.utc), norm.observations,
                          TOPOLOGY, SIGNATURES)
    series = sorted((o for o in norm.observations if o.metric == "interface_errors"), key=lambda o: o.observed_at)
    assert [_interval_delta(ctx, o) for o in series] == [None, 12, 18, None, 5]


def test_report_with_deltas_still_validates_against_the_schema():
    import jsonschema
    schema = json.loads((ROOT / "schemas" / "report-1.0.json").read_text(encoding="utf-8"))
    jsonschema.validate(run(fixture("congestion")).model_dump(mode="json"), schema)


# ---------------------------------------------------------------- P4-2


def with_stale(fx: dict, device: str) -> dict:
    """Add one out-of-window latency sample on `device` (monitoring gap)."""
    x = copy.deepcopy(fx)
    x["records"].append({"timestamp": "2026-11-01T09:20:00Z", "device_id": device, "source_id": "client-probe",
                         "metrics": {"latency_ms": 12.0}})
    return x


def test_baseline_false_report_is_healthy():
    assert run(fixture("false_report")).status == "healthy"


def test_stale_data_outside_the_area_does_not_block_healthy():
    # Complaint is about the Library (SW_CAMPUS_10); a gap on a hostel access switch is unrelated.
    report = run(with_stale(fixture("false_report"), "SW_CAMPUS_4"))
    assert report.status == "healthy", report.limitations
    assert any("scoped to the area in question" in l and "SW_CAMPUS_4" in l for l in report.limitations)


def test_stale_data_inside_the_area_still_blocks_healthy():
    report = run(with_stale(fixture("false_report"), "SW_CAMPUS_10"))
    assert report.status == "insufficient_evidence"
    assert any("no missing or stale data in the area in question" in l for l in report.limitations)


def test_complaint_naming_nothing_keeps_the_whole_network_rule():
    fx = with_stale(fixture("false_report"), "SW_CAMPUS_4")
    fx["complaint"] = "Users say it is slow"
    assert run(fx).status == "insufficient_evidence"


@pytest.mark.parametrize("complaint,inside,outside", [
    ("Library users say the internet is slow", "SW_CAMPUS_10", "SW_CAMPUS_4"),
    ("CSE department wired connections are flaky", "DEPT_UPLINK_01", "HOSTEL_UPLINK_01"),
    ("Switch SW_CAMPUS_5 rebooted overnight", "ACC_LINK_05", "SW_CAMPUS_7"),
])
def test_area_is_the_named_part_plus_its_path_and_services(complaint, inside, outside):
    from netcoop.agents.base import IncidentContext
    from netcoop.agents.correlation import _area
    from datetime import datetime, timezone
    ctx = IncidentContext("INC-T", datetime(2026, 11, 1, 10, 5, tzinfo=timezone.utc), [], TOPOLOGY, SIGNATURES,
                          complaint=complaint)
    area, named = _area(ctx)
    assert named and inside in area and outside not in area
    assert {"CORE_RTR_01", "SRV_DNS_DHCP_01", "SRV_LINK_01"} <= area
