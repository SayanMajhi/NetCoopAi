"""Submission polish (contract-1.0.4): rejected messages reach the report, confidence never says more
than the status, every evidence item is interpreted, every report has a one-line summary, and
excluded readings are named."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from netcoop.orchestrator.run import default_components, load_signatures, load_topology, run_incident
from netcoop.protocol.models import Report

DEV = Path(__file__).resolve().parents[1] / "scenarios" / "dev"
TOPOLOGY = load_topology()
SIGNATURES = load_signatures()


def _run(fixture: dict) -> Report:
    report = run_incident(fixture, TOPOLOGY, SIGNATURES, components=default_components("local")).report
    Report.model_validate(report.model_dump(mode="json"))
    return report


def _scenario(name: str) -> Report:
    return _run(json.loads((DEV / name).read_text(encoding="utf-8")))


@pytest.mark.parametrize("name, code", [("unsupported_2_x.json", "SCHEMA_VERSION_UNSUPPORTED"),
                                        ("missing_message_field.json", "MISSING_FIELD"),
                                        ("duplicate_message.json", "DUPLICATE_MESSAGE")])
def test_rejected_messages_are_named_in_the_report(name: str, code: str) -> None:
    report = _scenario(name)
    assert any(e.startswith(code) for e in report.validation_errors), report.validation_errors
    assert "validation_errors" in report.summary


def test_confidence_is_capped_by_status() -> None:
    reports = [_scenario(p.name) for p in sorted(DEV.glob("*.json")) if p.name != "malformed_input.json"]
    probable = [r.confidence for r in reports if r.status == "probable"]
    diagnosed = [r.confidence for r in reports if r.status == "diagnosed"]
    assert probable and diagnosed
    assert max(probable) < 0.70 <= min(diagnosed)
    assert all(r.confidence is None or r.confidence <= 0.95 for r in reports)
    capped = _scenario("congestion.json")
    assert any(lim.startswith("heuristic-v1 score 1.00 reported as 0.95") for lim in capped.limitations)


def test_every_report_has_a_summary_and_interpreted_evidence() -> None:
    for path in sorted(DEV.glob("*.json")):
        if path.name == "malformed_input.json":
            continue
        report = _scenario(path.name)
        assert report.summary, path.name
        assert all(e.interpretation for e in report.supporting_evidence), path.name
    congestion = _scenario("congestion.json")
    assert "above critical 90 percent" in [e.interpretation for e in congestion.supporting_evidence]
    assert congestion.summary.startswith("Diagnosed link congestion on HOSTEL_UPLINK_01")


def test_malformed_readings_are_named_in_limitations() -> None:
    fixture = json.loads((DEV / "congestion.json").read_text(encoding="utf-8"))
    for record in fixture["records"][:20]:
        record["metrics"] = {k: "NaN" for k in record["metrics"]}
    report = _run(fixture)
    # 20 records carry 35 readings; each one is flagged malformed and kept as null, never 0.
    assert any(lim.startswith("data quality: 35 malformed reading(s) excluded from the checks")
               for lim in report.limitations), report.limitations[:3]
