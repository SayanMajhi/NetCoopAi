from datetime import datetime, timezone

import pytest

from netcoop.agents.base import BaseAgent, IncidentContext
from netcoop.protocol.models import Envelope, MessageType, Observation

CLOCK = datetime(2026, 11, 1, 10, 5, tzinfo=timezone.utc)


def obs(eid: str, minute: int, metric: str = "link_utilization_pct", value: float = 90.0,
        device_id: str | None = None, link_id: str | None = "HOSTEL_UPLINK_01") -> Observation:
    return Observation(
        evidence_id=eid, source_id="switch-telemetry",
        observed_at=datetime(2026, 11, 1, 10, minute, tzinfo=timezone.utc),
        device_id=device_id, link_id=link_id, metric=metric, value=value, unit="percent",
        original_field=f"metrics.{metric}", original_value=value,
    )


class DemoAgent(BaseAgent):
    agent_id = "performance_agent"
    abbrev = "PERF"


@pytest.fixture
def ctx() -> IncidentContext:
    observations = [
        obs("EV-002", 3), obs("EV-001", 1),
        obs("EV-003", 2, metric="cpu_pct", value=40.0, device_id="SW_CAMPUS_1", link_id=None),
    ]
    return IncidentContext(incident_id="INC-0101", clock=CLOCK, observations=observations,
                           topology={}, signatures={"thresholds": {"cpu_pct": {"warn": 80, "critical": 95}}})


def test_lookups(ctx: IncidentContext) -> None:
    assert ctx.evidence("EV-003").metric == "cpu_pct"
    assert ctx.evidence("EV-999") is None
    assert [o.evidence_id for o in ctx.by_metric("link_utilization_pct")] == ["EV-001", "EV-002"]
    assert [o.evidence_id for o in ctx.for_target("SW_CAMPUS_1")] == ["EV-003"]
    assert [o.evidence_id for o in ctx.for_target("HOSTEL_UPLINK_01", "cpu_pct")] == []
    assert ctx.threshold("cpu_pct")["critical"] == 95 and ctx.threshold("latency_ms") == {}


def test_clock_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError):
        IncidentContext(incident_id="INC-1", clock=datetime(2026, 11, 1), observations=[], topology={}, signatures={})


def test_make_is_deterministic_and_valid(ctx: IncidentContext) -> None:
    a, b = DemoAgent(), DemoAgent()
    m1 = a.make(ctx, "correlation_agent", MessageType.RESPONSE, {"answer": "x"}, "INC-0101-ORCH-01")
    m2 = a.make(ctx, "correlation_agent", MessageType.RESPONSE, {"answer": "y"}, None)
    assert (m1.message_id, m2.message_id) == ("INC-0101-PERF-01", "INC-0101-PERF-02")
    assert b.make(ctx, "x", MessageType.RESPONSE, {}, None).message_id == "INC-0101-PERF-01"
    assert m1.timestamp == CLOCK and m1.sender == "performance_agent"
    Envelope.model_validate(m1.model_dump(mode="json"))


def test_finding_builds_valid_message(ctx: IncidentContext) -> None:
    msg = DemoAgent().finding(ctx, "correlation_agent", "INC-0101-ORCH-01",
                              hypothesis="link_congestion@HOSTEL_UPLINK_01", stance="supports",
                              evidence_ids=["EV-002", "EV-001", "EV-002"], reason="sustained high utilization",
                              consumed_message_ids=["INC-0101-INF-01"])
    p = msg.payload
    assert msg.type == MessageType.FINDING.value
    assert p["finding_id"] == "F-PERF-01" and p["evidence_ids"] == ["EV-001", "EV-002"]
    assert p["target_hypothesis"] == p["hypothesis"] and p["consumed_message_ids"] == ["INC-0101-INF-01"]


def test_finding_refuses_unknown_evidence(ctx: IncidentContext) -> None:
    with pytest.raises(ValueError, match="EV-404"):
        DemoAgent().finding(ctx, "correlation_agent", None, hypothesis="link_congestion@HOSTEL_UPLINK_01",
                            stance="supports", evidence_ids=["EV-404"], reason="x")


def test_finding_still_enforces_protocol(ctx: IncidentContext) -> None:
    with pytest.raises(ValueError):
        DemoAgent().finding(ctx, "correlation_agent", None, hypothesis="aliens@X",
                            stance="supports", evidence_ids=["EV-001"], reason="x")
