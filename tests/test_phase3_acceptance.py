"""Phase 3 acceptance criteria (docs/PHASE3_TASKS.md) with the real pipeline and scenarios.

labels.json is read here, by the test, never by runtime code.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from netcoop.orchestrator.run import (
    RunResult,
    default_components,
    load_signatures,
    load_topology,
    run_incident,
    write_outputs,
)
from netcoop.protocol.models import MessageType, Report

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "scenarios" / "dev"
LABELS = json.loads((ROOT / "scenarios" / "labels.json").read_text(encoding="utf-8"))["scenarios"]
TOPOLOGY, SIGNATURES = load_topology(), load_signatures()
TOPOLOGY_IDS = {n["id"] for n in TOPOLOGY["nodes"]} | {l["link_id"] for l in TOPOLOGY["links"]}
WITH_CANDIDATES = ["congestion", "interface_fault", "weak_wifi", "ambiguous_congestion_errors", "unit_mismatch",
                   "schema_0_9", "duplicate_message"]


def load(name: str):
    try:
        return json.loads((DEV / name).read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def run(name: str) -> RunResult:
    return run_incident(load(f"{name}.json" if not name.endswith(".json") else name), TOPOLOGY, SIGNATURES,
                        components=default_components(connectivity="local"), incident_id=f"INC-{name}")


# 5. accuracy on the 17 labelled scenarios ------------------------------------------------


@pytest.mark.parametrize("name", sorted(LABELS))
def test_matches_label(name: str) -> None:
    result, label = run(name), LABELS[name]
    report = result.report
    cause = report.suspected_root_cause.split("@")[0] if report.suspected_root_cause else None
    assert report.status == label["status"], report.limitations
    assert cause == label["cause"]
    assert sorted(report.affected_devices + report.affected_segments) == sorted(label["affected_ids"])
    errors = {m.payload["code"] for m in result.messages if m.type == MessageType.ERROR.value}
    assert set(label["expected_errors"]) <= errors


# 3. the report ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(LABELS))
def test_report_integrity(name: str) -> None:
    result = run(name)
    report = Report.model_validate(result.report.model_dump(mode="json"))
    if report.status == "invalid_input":
        return
    assert set(report.affected_devices + report.affected_segments) <= TOPOLOGY_IDS
    finding_ids = {m.payload["finding_id"] for m in result.findings}
    assert set(report.agent_findings) <= finding_ids
    assert report.recommended_actions and report.limitations is not None
    assert report.confidence is not None
    if not any(lim.startswith("correlation_failed") for lim in report.limitations):
        assert report.confidence_method == "heuristic-v1"


def test_cited_evidence_exists_in_the_incident() -> None:
    fixture = load("congestion.json")
    comps = default_components(connectivity="local")
    obs = {o.evidence_id for o in comps.normalize(fixture, TOPOLOGY, SIGNATURES).observations}
    report = run_incident(fixture, TOPOLOGY, SIGNATURES, components=comps).report
    assert report.supporting_evidence and {e.evidence_id for e in report.supporting_evidence} <= obs


@pytest.mark.parametrize("name", [n for n in sorted(LABELS) if LABELS[n]["status"] == "diagnosed"])
def test_diagnosis_cites_two_independent_raw_sources(name: str) -> None:
    """C1: ARCHITECTURE §7 "the report cites two distinct raw sources"."""
    report = run(name).report
    assert len({e.source_id for e in report.supporting_evidence}) >= 2


@pytest.mark.parametrize("name", WITH_CANDIDATES)
def test_confidence_is_computed_not_a_placeholder(name: str) -> None:
    """C2: heuristic-v1 everywhere a hypothesis exists."""
    report = run(name).report
    assert report.confidence_method == "heuristic-v1" and 0 < report.confidence <= 1


def test_markdown_report_is_written(tmp_path: Path) -> None:
    result = run("congestion")
    report_path, _ = write_outputs(result, tmp_path / "r", tmp_path / "t")
    md = report_path.with_suffix(".md").read_text(encoding="utf-8")
    assert "# Incident INC-0103: diagnosed" in md and "link_congestion@HOSTEL_UPLINK_01" in md


# 2. QUERY ------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", WITH_CANDIDATES)
def test_query_verifies_the_leader(name: str) -> None:
    msgs = run(name).messages
    queries = [m for m in msgs if m.type == "QUERY"]
    responses = [m for m in msgs if m.type == "RESPONSE" and m.sender != "connectivity_agent"]
    assert 1 <= len(queries) <= 2
    assert {r.payload["query_id"] for r in responses} == {q.payload["query_id"] for q in queries}
    assert all(r.in_reply_to == q.message_id for q, r in zip(queries, responses))


def test_ambiguous_query_changes_the_ranking() -> None:
    [decision] = [m for m in run("ambiguous_congestion_errors").messages if m.type == "DECISION"]
    history = [lim for lim in decision.payload["limitations"] if lim.startswith(("before Q-", "after Q-"))]
    leaders = [h.split(": ", 1)[1].split(" ", 1)[0] for h in history]
    assert leaders[0] != leaders[-1], history              # the leader before the QUERY rounds is not the leader after
    assert decision.payload["ranking"][0]["hypothesis"] == "link_congestion@DEPT_UPLINK_01"


# 4. robustness -------------------------------------------------------------------------------


def test_0_9_message_is_upgraded_with_a_visible_record() -> None:
    result = run("schema_0_9")
    assert any(m.sender == "legacy_switch_monitor" and m.type == "FINDING" for m in result.findings)
    assert any("upgraded netcoop/0.9 to netcoop/1.0" in line for line in result.report.normalization_log)


def test_finding_citing_unknown_evidence_is_excluded() -> None:
    fixture = load("congestion.json")
    bogus = {"protocol": "netcoop/1.0", "message_id": "INC-0103-EXT-09", "incident_id": "INC-0103", "in_reply_to": None,
             "sender": "external_monitor", "recipient": "correlation_agent", "type": "FINDING",
             "timestamp": "2026-11-01T10:05:00Z",
             "payload": {"finding_id": "F-EXT-09", "hypothesis": "hardware_failure@SW_CAMPUS_1", "stance": "supports",
                         "target_hypothesis": "hardware_failure@SW_CAMPUS_1", "evidence_ids": ["EV-999"],
                         "reason": "made-up evidence"}}
    result = run_incident({**fixture, "messages": [bogus]}, TOPOLOGY, SIGNATURES,
                          components=default_components(connectivity="local"))
    errors = [m for m in result.messages if m.type == "ERROR" and m.payload["code"] == "UNKNOWN_EVIDENCE"]
    assert errors and errors[0].payload["offending_message_id"] == "INC-0103-EXT-09"
    assert "F-EXT-09" not in result.report.agent_findings
    assert result.report.suspected_root_cause == "link_congestion@HOSTEL_UPLINK_01"


def test_correlation_timeout_falls_back_after_one_retry() -> None:
    result = run("correlation_timeout")
    report = result.report
    assert report.status == "insufficient_evidence" and report.suspected_root_cause is None
    assert any(lim.startswith("correlation_failed") for lim in report.limitations)
    [error] = [m for m in result.messages if m.type == "ERROR" and m.payload["code"] == "AGENT_TIMEOUT"]
    assert "attempt 2" in error.payload["detail"] and "after one retry" in error.payload["detail"]
    assert report.agent_findings                            # the unweighed findings are still listed


def test_non_utc_offset_is_converted_and_logged() -> None:
    fixture = load("normal.json")
    record = fixture["records"][0]
    record["timestamp"] = record["timestamp"].replace("Z", "+00:00").replace("T10:", "T15:").replace("+00:00", "+05:00")
    result = run_incident(fixture, TOPOLOGY, SIGNATURES, components=default_components(connectivity="local"))
    assert any("converted to UTC" in line for line in result.report.normalization_log)


def test_deterministic() -> None:
    a, b = run("ambiguous_congestion_errors"), run("ambiguous_congestion_errors")
    assert a.report == b.report
    assert [m.model_dump(mode="json") for m in a.messages] == [m.model_dump(mode="json") for m in b.messages]
