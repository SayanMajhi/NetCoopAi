"""Pipeline wiring (orchestrator/run.py) with stand-in components.

The real normalizer and agents arrive from B and C; these fakes pin down the
contract the pipeline relies on, so their work plugs in without changes here.
"""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

import pytest

from netcoop.agents.base import BaseAgent, IncidentContext
from netcoop.normalizer.result import NormalizationResult
from netcoop.orchestrator.run import (
    Components,
    default_components,
    load_signatures,
    load_topology,
    run_incident,
    write_outputs,
)
from netcoop.orchestrator.transport import local_transport
from netcoop.protocol.models import Envelope, MessageType, Observation, Report

CLOCK = datetime(2026, 11, 1, 10, 5, tzinfo=timezone.utc)
TOPOLOGY = load_topology()
SIGNATURES = load_signatures()
FIXTURE = {
    "incident_id": "INC-0101",
    "window_end": "2026-11-01T10:05:00Z",
    "records": [{"timestamp": "2026-11-01T10:01:00Z", "link_id": "HOSTEL_UPLINK_01",
                 "metrics": {"link_utilization_pct": 96.0}}],
}


def fake_normalize(fixture: dict[str, Any], topology: dict[str, Any], signatures: dict[str, Any]) -> NormalizationResult:
    obs = [
        Observation(evidence_id="EV-001", source_id="switch-telemetry", observed_at=CLOCK, link_id="HOSTEL_UPLINK_01",
                    metric="link_utilization_pct", value=96.0, unit="percent",
                    original_field="metrics.link_utilization_pct", original_value=96.0),
        Observation(evidence_id="EV-002", source_id="switch-telemetry", observed_at=CLOCK, device_id="SW_CAMPUS_1",
                    metric="interface_errors", value=12.0, unit="count",
                    original_field="metrics.interface_errors", original_value=12.0),
    ]
    return NormalizationResult(observations=obs, normalization_log=["cpu_utilization_pct -> cpu_pct"], window_end=CLOCK)


class FakePerformance(BaseAgent):
    agent_id, abbrev = "performance_agent", "PERF"

    def handle(self, msg: Envelope, ctx: IncidentContext) -> list[Envelope]:
        return [self.finding(ctx, "correlation_agent", msg.message_id, hypothesis="link_congestion@HOSTEL_UPLINK_01",
                             stance="supports", evidence_ids=["EV-001"], reason="utilization 96%")]


class FakeInfrastructure(BaseAgent):
    agent_id, abbrev = "infrastructure_agent", "INF"

    def handle(self, msg: Envelope, ctx: IncidentContext) -> list[Envelope]:
        return [self.finding(ctx, "correlation_agent", msg.message_id, hypothesis="interface_fault@SW_CAMPUS_1",
                             stance="contradicts", target_hypothesis="link_congestion@HOSTEL_UPLINK_01",
                             evidence_ids=["EV-002"], reason="errors rising")]


class CrashingAgent(BaseAgent):
    agent_id, abbrev = "infrastructure_agent", "INF"

    def handle(self, msg: Envelope, ctx: IncidentContext) -> list[Envelope]:
        raise RuntimeError("boom")


def fake_handle_bundle(messages: list[Envelope], observations: list[Observation], topology: dict) -> list[Envelope]:
    """Stand-in Connectivity logic: its scope depends on the peer findings it receives."""
    task = messages[0]
    peers = [m for m in messages[1:] if m.type == MessageType.FINDING.value]
    agent = BaseAgent()
    agent.agent_id, agent.abbrev = "connectivity_agent", "CONN"
    ctx = IncidentContext(incident_id=task.incident_id, clock=task.timestamp, observations=observations,
                          topology=topology, signatures={})
    if peers:
        return [agent.finding(ctx, "correlation_agent", task.message_id,
                              hypothesis="upstream_dependency@HOSTEL_UPLINK_01", stance="supports",
                              evidence_ids=["EV-001"], reason="peer congestion finding on the shared hostel uplink",
                              consumed_message_ids=[p.message_id for p in peers])]
    return [agent.finding(ctx, "correlation_agent", task.message_id, hypothesis="healthy@HOSTEL_UPLINK_01",
                          stance="supports", evidence_ids=["EV-001"], reason="no peer evidence; path reachable")]


def components(**overrides: Any) -> Components:
    base = dict(normalize=fake_normalize,
                agents={"performance_agent": FakePerformance(), "infrastructure_agent": FakeInfrastructure()},
                connectivity=local_transport(fake_handle_bundle))
    base.update(overrides)
    return Components(**base)


def run(**kw: Any):
    return run_incident(FIXTURE, TOPOLOGY, SIGNATURES, components=kw.pop("components", None) or components(), **kw)


def by_sender(result, sender: str) -> list[Envelope]:
    return [m for m in result.messages if m.sender == sender and m.type == MessageType.FINDING.value]


def test_end_to_end_report_is_schema_valid() -> None:
    result = run()
    Report.model_validate(result.report.model_dump(mode="json"))
    assert result.report.status == "insufficient_evidence" and result.report.suspected_root_cause is None
    assert result.report.agent_findings == ["F-CONN-01", "F-INF-01", "F-PERF-01"]
    assert result.report.evidence_sources == ["connectivity_agent", "infrastructure_agent", "performance_agent"]
    assert result.report.normalization_log == ["cpu_utilization_pct -> cpu_pct"]


def test_connectivity_receives_peer_findings_in_the_bundle() -> None:
    result = run()
    request = result.wire[0]["request"]
    task, *peers = request["messages"]
    assert task["type"] == "TASK" and task["recipient"] == "connectivity_agent"
    assert task["payload"]["peer_message_ids"] == [p["message_id"] for p in peers]
    assert {p["sender"] for p in peers} == {"performance_agent", "infrastructure_agent"}
    assert len(request["observations"]) == 2 and request["topology"]["links"]
    conn = by_sender(result, "connectivity_agent")[0]
    assert conn.payload["consumed_message_ids"] == [p["message_id"] for p in peers]


def test_control_run_changes_connectivity_conclusion() -> None:
    normal = by_sender(run(), "connectivity_agent")[0].payload
    control_result = run(control=True)
    control = by_sender(control_result, "connectivity_agent")[0].payload
    assert normal["hypothesis"] != control["hypothesis"]
    assert control["consumed_message_ids"] == []
    assert control_result.wire[0]["request"]["messages"][0]["payload"]["peer_message_ids"] == []
    assert any("control run" in lim for lim in control_result.report.limitations)


def test_every_message_is_protocol_valid_and_deterministic() -> None:
    a, b = run(), run()
    dump = lambda r: [m.model_dump(mode="json") for m in r.messages]
    assert dump(a) == dump(b) and a.report == b.report
    for m in dump(a):
        Envelope.model_validate(m)
    assert {m["timestamp"] for m in dump(a)} == {"2026-11-01T10:05:00Z"}


def test_agent_crash_becomes_typed_error_and_run_continues() -> None:
    result = run(components=components(agents={"performance_agent": FakePerformance(),
                                               "infrastructure_agent": CrashingAgent()}))
    errors = [m for m in result.messages if m.type == "ERROR"]
    assert errors and errors[0].payload["code"] == "AGENT_FAILURE"
    assert any("infrastructure_agent: AGENT_FAILURE" in lim for lim in result.report.limitations)
    assert result.report.status == "insufficient_evidence"


def test_connectivity_failure_is_reported_not_raised() -> None:
    def broken(*_: Any) -> list[Envelope]:
        raise ConnectionError("refused")
    result = run(components=components(connectivity=local_transport(broken)))
    assert result.wire[0]["error"].startswith("AGENT_FAILURE")
    assert any(m.type == "ERROR" and m.recipient == "connectivity_agent" for m in result.messages)


def test_bad_reply_from_connectivity_goes_through_the_bus() -> None:
    def bad_reply(messages: list[Envelope], *_: Any) -> list[Envelope]:
        msg = fake_handle_bundle(messages, *_)[0]
        return [msg.model_copy(update={"protocol": "netcoop/2.0"})]
    result = run(components=components(connectivity=local_transport(bad_reply)))
    assert not by_sender(result, "connectivity_agent")
    assert any(m.type == "ERROR" and m.payload["code"] == "SCHEMA_VERSION_UNSUPPORTED" for m in result.messages)


@pytest.mark.parametrize("fixture", [None, [], {"incident_id": "INC-X"}, {"incident_id": "INC-X", "records": []}])
def test_bad_fixture_gives_invalid_input(fixture: Any) -> None:
    result = run_incident(fixture, TOPOLOGY, SIGNATURES, components=components())
    assert result.report.status == "invalid_input" and result.report.confidence is None
    assert result.report.validation_errors


def test_normalizer_validation_errors_give_invalid_input() -> None:
    def rejecting(*_: Any) -> NormalizationResult:
        return NormalizationResult(validation_errors=["record 1 has unmapped metric 'foo'"])
    result = run(components=components(normalize=rejecting))
    assert result.report.status == "invalid_input"
    assert result.report.validation_errors == ["record 1 has unmapped metric 'foo'"]


def test_missing_components_degrade_gracefully() -> None:
    result = run(components=components(agents={}, connectivity=None, unavailable=["performance_agent unavailable"]))
    assert result.report.status == "insufficient_evidence"
    assert "performance_agent unavailable" in result.report.limitations
    assert result.findings == [] and result.wire == []


def test_no_normalizer_gives_failed() -> None:
    result = run(components=components(normalize=None, unavailable=["normalizer unavailable"]))
    assert result.report.status == "failed" and result.report.confidence is None


def test_injected_messages_pass_through_the_bus() -> None:
    v2 = json.loads((Path(__file__).resolve().parents[1] / "examples" / "message_v2_0.json").read_text(encoding="utf-8"))
    result = run_incident({**FIXTURE, "messages": [v2]}, TOPOLOGY, SIGNATURES, components=components())
    assert result.messages[0].type == "ERROR"
    assert result.messages[0].payload["code"] == "SCHEMA_VERSION_UNSUPPORTED"


def test_default_components_report_what_is_missing() -> None:
    comps = default_components(connectivity="local")
    # Until B and C merge their modules, each missing one is named, never a crash.
    for name in ("normalizer", "performance_agent", "infrastructure_agent", "connectivity_agent"):
        present = {"normalizer": comps.normalize, "performance_agent": comps.agents.get("performance_agent"),
                   "infrastructure_agent": comps.agents.get("infrastructure_agent"),
                   "connectivity_agent": comps.connectivity}[name]
        assert present is not None or any(u.startswith(name) for u in comps.unavailable)


def test_write_outputs(tmp_path) -> None:
    result = run()
    report_path, trace_path = write_outputs(result, tmp_path / "reports", tmp_path / "traces")
    assert json.loads(report_path.read_text(encoding="utf-8"))["incident_id"] == "INC-0101"
    lines = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert {line["kind"] for line in lines} == {"message", "wire"}
    assert lines[-1]["request"]["messages"][0]["type"] == "TASK"
