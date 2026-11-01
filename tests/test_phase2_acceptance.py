"""Phase 2 acceptance criteria (docs/PHASE2_TASKS.md), checked with the real normalizer,
agents and Connectivity logic on the labelled scenarios in scenarios/dev/.

Connectivity runs in-process here (local transport); the same Bundle crosses real HTTP
in tests/test_connectivity_service.py and in the live proof of the Phase 2 review pack.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from netcoop.orchestrator.run import RunResult, default_components, load_signatures, load_topology, run_incident
from netcoop.protocol.models import Envelope, MessageType, Report

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "scenarios" / "dev"
TOPOLOGY, SIGNATURES = load_topology(), load_signatures()
KEY_SIX = ["normal", "congestion", "interface_fault", "weak_wifi", "dns_failure", "ambiguous_congestion_errors"]
FAULTS_NEEDING_PEERS = ["congestion", "interface_fault", "weak_wifi", "ambiguous_congestion_errors"]


def run(name: str, control: bool = False) -> RunResult:
    fixture = json.loads((DEV / f"{name}.json").read_text(encoding="utf-8"))
    return run_incident(fixture, TOPOLOGY, SIGNATURES, components=default_components(connectivity="local"),
                        control=control)


def findings(result: RunResult, sender: str) -> list[dict]:
    return [m.payload for m in result.findings if m.sender == sender]


def faults(payloads: list[dict]) -> set[str]:
    return {p["hypothesis"] for p in payloads if p["stance"] == "supports" and not p["hypothesis"].startswith("healthy")}


def test_all_components_are_wired() -> None:
    assert default_components(connectivity="local").unavailable == []


# 3. Every agent emits schema-valid FINDINGs with a stance and real evidence IDs.
@pytest.mark.parametrize("name", KEY_SIX)
def test_findings_are_valid_and_cite_real_evidence(name: str) -> None:
    fixture = json.loads((DEV / f"{name}.json").read_text(encoding="utf-8"))
    observations = {o.evidence_id for o in default_components().normalize(fixture, TOPOLOGY, SIGNATURES).observations}
    result = run(name)
    senders = {m.sender for m in result.findings}
    assert {"performance_agent", "infrastructure_agent", "connectivity_agent"} <= senders
    for m in result.findings:
        Envelope.model_validate(m.model_dump(mode="json"))
        assert m.payload["stance"] in ("supports", "contradicts")
        assert m.payload["evidence_ids"] and set(m.payload["evidence_ids"]) <= observations, m.payload["finding_id"]


def test_infrastructure_contradicts_on_the_ambiguous_case() -> None:
    infra = findings(run("ambiguous_congestion_errors"), "infrastructure_agent")
    assert any(p["stance"] == "contradicts" for p in infra)


@pytest.mark.parametrize("name, expected", [
    ("congestion", {"performance_agent": "link_congestion@HOSTEL_UPLINK_01"}),
    ("interface_fault", {"infrastructure_agent": "interface_fault@ACC_LINK_07"}),
    ("weak_wifi", {"infrastructure_agent": "weak_wifi@HOSTEL_AP_01"}),
    ("dns_failure", {"connectivity_agent": "dns_failure@SRV_DNS_DHCP_01"}),
])
def test_specialists_see_their_fault(name: str, expected: dict[str, str]) -> None:
    result = run(name)
    for agent, hypothesis in expected.items():
        assert hypothesis in faults(findings(result, agent)), f"{agent} missed {hypothesis}"


@pytest.mark.parametrize("name", ["normal", "false_report", "counter_reset"])
def test_healthy_networks_raise_no_fault(name: str) -> None:
    result = run(name)
    for agent in ("performance_agent", "infrastructure_agent", "connectivity_agent"):
        assert not faults(findings(result, agent)), f"{agent} raised a false fault on {name}"


# 4. Connectivity consumes peer findings and its conclusion changes without them.
@pytest.mark.parametrize("name", FAULTS_NEEDING_PEERS)
def test_connectivity_uses_peers_and_control_run_differs(name: str) -> None:
    normal, control = run(name), run(name, control=True)
    conn_normal, conn_control = findings(normal, "connectivity_agent"), findings(control, "connectivity_agent")
    consumed = {mid for p in conn_normal for mid in p["consumed_message_ids"]}
    peer_ids = {m.message_id for m in normal.findings if m.sender in ("performance_agent", "infrastructure_agent")}
    assert consumed and consumed <= peer_ids
    claims = lambda payloads: {(p["hypothesis"], p["stance"]) for p in payloads}   # noqa: E731
    assert claims(conn_normal) != claims(conn_control)
    assert not any(p["consumed_message_ids"] for p in conn_control)


def test_connectivity_scopes_congestion_with_its_own_checks() -> None:
    conn = findings(run("congestion"), "connectivity_agent")
    [scope] = [p for p in conn if p["hypothesis"] == "link_congestion@HOSTEL_UPLINK_01"]
    assert scope["stance"] == "supports" and len(scope["consumed_message_ids"]) == 2
    assert "blast radius" in scope["reason"]
    assert not any(p["hypothesis"].startswith("upstream_dependency") for p in conn)


# --------------------------------------------------------------------------- Phase 2 review (R1, R2, R4)

@pytest.mark.parametrize("name, control", [(n, True) for n in FAULTS_NEEDING_PEERS] + [("missing_stale", False)])
def test_r1_connectivity_never_supports_health(name: str, control: bool) -> None:
    conn = findings(run(name, control=control), "connectivity_agent")
    assert not [p["hypothesis"] for p in conn if p["stance"] == "supports" and p["hypothesis"].startswith("healthy@")]


def test_r1_missing_stale_gives_no_path_health_claim() -> None:
    conn = findings(run("missing_stale"), "connectivity_agent")
    assert {p["hypothesis"] for p in conn if p["hypothesis"].startswith("healthy@")} <= {"healthy@SRV_DNS_DHCP_01"}


@pytest.mark.parametrize("name", FAULTS_NEEDING_PEERS + ["schema_0_9", "duplicate_message", "unit_mismatch"])
def test_r2_connectivity_never_recites_consumed_peer_evidence(name: str) -> None:
    result = run(name)
    peer_evidence = {m.message_id: set(m.payload["evidence_ids"]) for m in result.findings}
    for p in findings(result, "connectivity_agent"):
        consumed = set().union(*(peer_evidence.get(mid, set()) for mid in p["consumed_message_ids"]))
        assert not set(p["evidence_ids"]) & consumed, p["finding_id"]


@pytest.mark.parametrize("name", FAULTS_NEEDING_PEERS)
def test_r2_no_upstream_dependency_for_a_single_fault_target(name: str) -> None:
    assert not [h for h in faults(findings(run(name), "connectivity_agent")) if h.startswith("upstream_dependency")]


def test_r2_weak_wifi_checks_the_ap_uplink() -> None:
    conn = findings(run("weak_wifi"), "connectivity_agent")
    [uplink] = [p for p in conn if p["hypothesis"] == "healthy@HOSTEL_AP_LINK_01"]
    assert uplink["stance"] == "contradicts" and uplink["consumed_message_ids"]


def _with_extra_error_growth(name: str) -> dict:
    """AI 3's probe: +1 interface error per minute on every interface_errors series."""
    fixture = json.loads((DEV / f"{name}.json").read_text(encoding="utf-8"))
    seen: dict[str, int] = {}
    for record in fixture["records"]:
        if "interface_errors" in record["metrics"]:
            key = record.get("link_id") or record.get("device_id")
            seen[key] = seen.get(key, -1) + 1
            record["metrics"]["interface_errors"] += seen[key]
    return fixture


@pytest.mark.parametrize("name", ["normal", "false_report"])
def test_r4_healthy_network_with_counter_growth_raises_no_fault(name: str) -> None:
    for fixture in (json.loads((DEV / f"{name}.json").read_text(encoding="utf-8")), _with_extra_error_growth(name)):
        result = run_incident(fixture, TOPOLOGY, SIGNATURES, components=default_components(connectivity="local"))
        assert not faults([m.payload for m in result.findings]), [m.payload["hypothesis"] for m in result.findings
                                                                   if m.payload["stance"] == "supports"]


def test_trace_holds_the_wire_request_and_response() -> None:
    result = run("congestion")
    [wire] = result.wire
    task, *peers = wire["request"]["messages"]
    assert task["type"] == "TASK" and task["payload"]["peer_message_ids"] == [p["message_id"] for p in peers]
    assert wire["request"]["observations"] and wire["request"]["topology"]["links"]
    assert wire["response"]["messages"] and wire["error"] is None


# 5. netcoop run returns a schema-valid report for the six key fixtures.
@pytest.mark.parametrize("name", KEY_SIX)
def test_report_is_schema_valid(name: str) -> None:
    report = run(name).report
    Report.model_validate(report.model_dump(mode="json"))
    assert {"connectivity_agent", "infrastructure_agent", "performance_agent"} <= set(report.evidence_sources)


# 6. Two runs of the same fixture are identical (no wall-clock in messages or reports).
@pytest.mark.parametrize("name", ["congestion", "ambiguous_congestion_errors"])
def test_runs_are_deterministic(name: str) -> None:
    a, b = run(name), run(name)
    assert [m.model_dump(mode="json") for m in a.messages] == [m.model_dump(mode="json") for m in b.messages]
    assert a.report == b.report and a.wire == b.wire


def test_no_message_is_silently_dropped() -> None:
    result = run("duplicate_message")
    assert any(m.type == MessageType.ERROR.value and m.payload["code"] == "DUPLICATE_MESSAGE" for m in result.messages)


# ARCHITECTURE.md §3: the acceptance run ingests untouched organizer-generator output.
GENERATOR_RAW = sorted((ROOT / "data" / "generator_raw").glob("*.json"))


@pytest.mark.parametrize("path", GENERATOR_RAW, ids=lambda p: p.name)
def test_untouched_generator_output_runs_end_to_end(path: Path) -> None:
    raw = json.loads(path.read_text(encoding="utf-8"))
    result = run_incident(raw, TOPOLOGY, SIGNATURES, components=default_components(connectivity="local"),
                          incident_id=f"INC-{path.stem}")
    Report.model_validate(result.report.model_dump(mode="json"))
    assert result.report.status != "invalid_input", result.report.validation_errors
    assert result.report.incident_id == f"INC-{path.stem}"
    assert any("cpu_utilization_pct -> cpu_pct" in line for line in result.report.normalization_log)
    # One reading per metric: agents must abstain (no guessing) and the report must say why.
    assert not [m for m in result.findings if m.payload["stance"] == "supports"
                and not m.payload["hypothesis"].startswith("healthy")]
    assert any(lim.startswith("sparse telemetry") for lim in result.report.limitations)


def test_dense_scenarios_are_not_flagged_sparse() -> None:
    assert not any(lim.startswith("sparse telemetry") for lim in run("congestion").report.limitations)


@pytest.mark.parametrize("scenario, status", [
    ("scenarios/dev/congestion.json", "diagnosed"),
    ("data/generator_raw/normal_unseeded.json", "insufficient_evidence"),
    ("scenarios/dev/malformed_input.json", "invalid_input"),
    ("scenarios/dev/does_not_exist.json", "invalid_input"),
])
def test_cli_run_writes_report_and_trace(scenario: str, status: str, tmp_path: Path, capsys) -> None:
    from netcoop.cli import main
    assert main(["run", str(ROOT / scenario), "--local", "--out", str(tmp_path / "r"), "--traces", str(tmp_path / "t")]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == status
    saved = json.loads((tmp_path / "r" / f"{printed['incident_id']}.json").read_text(encoding="utf-8"))
    assert saved == printed and (tmp_path / "t" / f"{printed['incident_id']}.jsonl").exists()
    if status == "invalid_input":
        assert printed["validation_errors"] and printed["confidence"] is None


def test_cli_control_run_does_not_overwrite_the_normal_run(tmp_path: Path, capsys) -> None:
    from netcoop.cli import main
    scenario = str(ROOT / "scenarios/dev/congestion.json")
    for extra in ([], ["--control"]):
        main(["run", scenario, "--local", "--out", str(tmp_path / "r"), "--traces", str(tmp_path / "t"), *extra])
    capsys.readouterr()
    assert sorted(f.name for f in (tmp_path / "r").iterdir()) == ["INC-0103--control.json", "INC-0103--control.md",
                                                              "INC-0103.json", "INC-0103.md"]
    assert sorted(f.name for f in (tmp_path / "t").iterdir()) == ["INC-0103--control.jsonl", "INC-0103.jsonl"]
