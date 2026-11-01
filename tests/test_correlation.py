"""Correlation (Phase 3): signature checks, heuristic-v1 arithmetic, gates, QUERY rounds."""
from __future__ import annotations

from typing import Any

from netcoop.agents.base import BaseAgent, IncidentContext
from netcoop.agents.correlation import CorrelationAgent
from netcoop.agents.infrastructure import InfrastructureAgent
from netcoop.agents.performance import PerformanceAgent
from netcoop.protocol.models import Envelope, MessageType, Observation
from tests.evidence_builders import CLOCK, TOPOLOGY, context, observations, signatures

REAL = signatures(thresholds={})          # signatures-1.0.yaml as committed


def obs(spec: list[tuple[str, str, list[Any], str]]) -> list[Observation]:
    """(target, metric, values, source) rows, renumbered so evidence IDs stay unique."""
    out: list[Observation] = []
    for target, metric, values, source in spec:
        for o in observations([(target, metric, values)], source=source):
            out.append(o.model_copy(update={"evidence_id": f"EV-{len(out) + 1:03d}"}))
    return out


def agent(agent_id: str, abbrev: str) -> BaseAgent:
    a = BaseAgent()
    a.agent_id, a.abbrev = agent_id, abbrev
    return a


def finding(ctx: IncidentContext, sender: str, hypothesis: str, evidence: list[str], stance: str = "supports",
            target: str | None = None, score: float | None = None) -> Envelope:
    abbrev = {"performance_agent": "PERF", "infrastructure_agent": "INF", "connectivity_agent": "CONN"}[sender]
    return agent(sender, abbrev).finding(ctx, "correlation_agent", None, hypothesis=hypothesis, stance=stance,
                                         target_hypothesis=target, evidence_ids=evidence, reason="test", score=score)


CONGESTED = [("HOSTEL_UPLINK_01", "link_utilization_pct", [92, 95, 96, 94, 97], "switch-telemetry"),
             ("SW_CAMPUS_1", "queue_drops", [100, 400, 800, 1300, 1900], "switch-telemetry"),
             ("SW_CAMPUS_3", "latency_ms", [180, 220, 240, 230, 250], "client-probe")]


def ctx_for(spec) -> IncidentContext:
    return context(obs(spec), REAL)


# --------------------------------------------------------------------------- heuristic-v1, by hand


def test_heuristic_v1_arithmetic_matches_the_hand_computation() -> None:
    ctx = ctx_for(CONGESTED)
    corr = CorrelationAgent()
    [c] = corr.rank(ctx, [finding(ctx, "performance_agent", "link_congestion@HOSTEL_UPLINK_01", ["EV-001"]),
                          finding(ctx, "connectivity_agent", "link_congestion@HOSTEL_UPLINK_01", ["EV-011"])])
    # required: utilization sustained above 90 -> pass (1/1); corroborating: drops rising + latency -> 2 >= 1;
    # severity unverified 0.5 -> signal = 0.6*1 + 0.2*1 + 0.2*0.5 = 0.9
    assert c.factors == {"signal_fit": 0.9, "topology_fit": 1.0, "time_fit": 1.0, "independent_source_fit": 1.0,
                         "contradiction_strength": 0.0, "coverage": 1.0, "severity": 0.5}
    # raw = 0.40*0.9 + 0.25*1 + 0.20*1 + 0.15*1 - 0.15*0 = 0.96; confidence = round(0.96 * 1.0, 2)
    assert c.confidence == 0.96


def test_contradiction_and_topology_lower_the_score_as_documented() -> None:
    ctx = ctx_for(CONGESTED)
    corr = CorrelationAgent()
    corr.rank(ctx, [
        finding(ctx, "performance_agent", "link_congestion@HOSTEL_UPLINK_01", ["EV-001"]),
        finding(ctx, "infrastructure_agent", "interface_fault@HOSTEL_UPLINK_01", ["EV-006"], "contradicts",
                "link_congestion@HOSTEL_UPLINK_01", score=0.4)])
    assert set(corr.candidates) == {"link_congestion@HOSTEL_UPLINK_01", "interface_fault@HOSTEL_UPLINK_01"}  # alternative nominated
    c = corr.candidates["link_congestion@HOSTEL_UPLINK_01"]
    # no Connectivity scope: topology 0.5; contradiction 0.4
    # raw = 0.36 + 0.25*0.5 + 0.20 + 0.15 - 0.15*0.4 = 0.775 -> confidence 0.78 (rounded half-even on 0.775 -> 0.78)
    assert c.factors["topology_fit"] == 0.5 and c.factors["contradiction_strength"] == 0.4
    assert c.confidence == round(0.36 + 0.125 + 0.20 + 0.15 - 0.06, 2)


def test_one_source_halves_independence_and_blocks_diagnosis() -> None:
    spec = [(t, m, v, "switch-telemetry") for t, m, v, _ in CONGESTED]
    ctx = ctx_for(spec)
    corr = CorrelationAgent()
    corr.rank(ctx, [finding(ctx, "performance_agent", "link_congestion@HOSTEL_UPLINK_01", ["EV-001"])])
    _, info = corr.decide(ctx, None)
    assert corr.candidates["link_congestion@HOSTEL_UPLINK_01"].factors["independent_source_fit"] == 0.5
    assert info["status"] == "probable"
    assert any("fewer than two independent raw sources" in lim for lim in info["limitations"])


def test_missing_required_evidence_lowers_coverage_not_a_guess() -> None:
    ctx = ctx_for([("SW_CAMPUS_3", "latency_ms", [180, 220, 240, 230, 250], "client-probe")])
    [c] = CorrelationAgent().rank(ctx, [finding(ctx, "performance_agent", "link_congestion@HOSTEL_UPLINK_01", ["EV-001"])])
    assert c.coverage == 0.0 and c.confidence == 0.0 and not c.required_pass


# --------------------------------------------------------------------------- gates


def test_close_rival_blocks_diagnosis_and_is_named() -> None:
    spec = CONGESTED + [("HOSTEL_UPLINK_01", "interface_errors", [0, 30, 60, 95, 130], "switch-telemetry"),
                        ("SW_CAMPUS_3", "packet_loss_pct", [3, 4, 5, 4, 6], "client-probe")]
    ctx = ctx_for(spec)
    corr = CorrelationAgent()
    corr.rank(ctx, [finding(ctx, "performance_agent", "link_congestion@HOSTEL_UPLINK_01", ["EV-001"]),
                    finding(ctx, "infrastructure_agent", "interface_fault@HOSTEL_UPLINK_01", ["EV-016"])])
    _, info = corr.decide(ctx, None)
    assert info["status"] == "probable"
    assert any(lim.startswith("unresolved rival") for lim in info["limitations"])


def test_no_candidate_and_no_healthy_proof_is_insufficient() -> None:
    ctx = ctx_for([("SW_CAMPUS_1", "cpu_pct", [20, 22, 21, 25, 23], "switch-telemetry")])
    corr = CorrelationAgent()
    corr.rank(ctx, [])
    _, info = corr.decide(ctx, None)
    assert info["status"] == "insufficient_evidence"
    assert any("service checks" in lim for lim in info["limitations"])


def test_healthy_needs_normal_measurements_and_passing_checks() -> None:
    spec = [("HOSTEL_UPLINK_01", "link_utilization_pct", [30, 35, 33, 31, 34], "switch-telemetry"),
            ("SW_CAMPUS_1", "cpu_pct", [20, 22, 21, 25, 23], "switch-telemetry"),
            ("SW_CAMPUS_3", "dns_success", [True] * 5, "client-probe"),
            ("SW_CAMPUS_3", "reachability", [True] * 5, "icmp-monitor")]
    ctx = ctx_for(spec)
    corr = CorrelationAgent()
    corr.rank(ctx, [])
    _, info = corr.decide(ctx, None)
    assert info["status"] == "healthy" and info["healthy_confidence"] == 1.0


# --------------------------------------------------------------------------- QUERY


def test_query_verifies_the_leader_and_sets_severity() -> None:
    ctx = ctx_for(CONGESTED)
    corr = CorrelationAgent()
    corr.rank(ctx, [finding(ctx, "performance_agent", "link_congestion@HOSTEL_UPLINK_01", ["EV-001"])])
    [q] = corr.plan_queries(ctx)
    assert q.type == MessageType.QUERY.value and q.recipient == "performance_agent"
    assert q.payload["desired_measurement"] == "link_utilization_pct" and q.payload["target_id"] == "HOSTEL_UPLINK_01"
    response = PerformanceAgent().answer(q, ctx)
    assert response.type == "RESPONSE" and response.payload["evidence_ids"] == [f"EV-{i:03d}" for i in range(1, 6)]
    corr.absorb(response, ctx, [])
    c = corr.candidates["link_congestion@HOSTEL_UPLINK_01"]
    assert c.severity == 1.0 and "above 90%" in c.verified
    assert corr.plan_queries(ctx) == []                     # no rival: no second round


def test_never_more_than_two_query_rounds() -> None:
    spec = CONGESTED + [("HOSTEL_UPLINK_01", "interface_errors", [0, 30, 60, 95, 130], "switch-telemetry"),
                        ("SW_CAMPUS_3", "packet_loss_pct", [3, 4, 5, 4, 6], "client-probe")]
    ctx = ctx_for(spec)
    corr = CorrelationAgent()
    findings = [finding(ctx, "performance_agent", "link_congestion@HOSTEL_UPLINK_01", ["EV-001"]),
                finding(ctx, "infrastructure_agent", "interface_fault@HOSTEL_UPLINK_01", ["EV-016"])]
    corr.rank(ctx, findings)
    rounds = 0
    while (queries := corr.plan_queries(ctx)):
        rounds += 1
        for q in queries:
            owner = {"performance_agent": PerformanceAgent(), "infrastructure_agent": InfrastructureAgent()}[q.recipient]
            corr.absorb(owner.answer(q, ctx), ctx, findings)
        assert rounds <= 2
    assert rounds == 2


def test_a_failed_rival_still_allows_diagnosis() -> None:
    """R1 keeps the gate strict, not blind: a rival whose required check FAILS is refuted."""
    spec = CONGESTED + [("HOSTEL_UPLINK_01", "interface_errors", [10, 11, 11, 12, 12], "switch-telemetry"),
                        ("SW_CAMPUS_4", "packet_loss_pct", [0, 0, 0.1, 0, 0], "client-probe")]   # no downstream loss
    ctx = ctx_for(spec)
    corr = CorrelationAgent()
    corr.rank(ctx, [finding(ctx, "performance_agent", "link_congestion@HOSTEL_UPLINK_01", ["EV-001"]),
                    finding(ctx, "connectivity_agent", "link_congestion@HOSTEL_UPLINK_01", ["EV-011"]),
                    finding(ctx, "infrastructure_agent", "interface_fault@HOSTEL_UPLINK_01", ["EV-016"])])
    rival = corr.candidates["interface_fault@HOSTEL_UPLINK_01"]
    assert rival.refuted and not rival.unresolved
    _, info = corr.decide(ctx, None)
    assert info["status"] == "diagnosed" and rival not in info["alternatives"]


def test_utilization_dipping_into_the_warn_band_is_still_sustained() -> None:
    """A dip between warn and critical is hysteresis, not recovery (vocabulary sustained_above_critical)."""
    ctx = ctx_for([("HOSTEL_UPLINK_01", "link_utilization_pct", [97.7, 100, 89.7, 95.7, 100], "switch-telemetry")])
    [c] = CorrelationAgent().rank(ctx, [finding(ctx, "performance_agent", "link_congestion@HOSTEL_UPLINK_01", ["EV-001"])])
    assert c.required[0].state == "pass"


def test_elevated_but_unsteady_utilization_is_inconclusive_not_refuted() -> None:
    ctx = ctx_for([("HOSTEL_UPLINK_01", "link_utilization_pct", [100, 91.9, 84.7, 70.0, 83.0], "switch-telemetry")])
    [c] = CorrelationAgent().rank(ctx, [finding(ctx, "performance_agent", "link_congestion@HOSTEL_UPLINK_01", ["EV-001"])])
    assert c.required[0].state == "inconclusive" and not c.refuted and c.unresolved


def test_utilization_well_below_warn_refutes() -> None:
    ctx = ctx_for([("HOSTEL_UPLINK_01", "link_utilization_pct", [40, 42, 39, 41, 38], "switch-telemetry")])
    [c] = CorrelationAgent().rank(ctx, [finding(ctx, "performance_agent", "link_congestion@HOSTEL_UPLINK_01", ["EV-001"])])
    assert c.required[0].state == "fail" and c.refuted


def test_a_gap_ends_a_sustained_run() -> None:
    from datetime import timedelta
    obs = [o for o in observations([("HOSTEL_UPLINK_01", "link_utilization_pct", [95, 96, 97, 98, 99])]) if o.evidence_id != "EV-003"]
    ctx = context(obs, REAL)
    [c] = CorrelationAgent().rank(ctx, [finding(ctx, "performance_agent", "link_congestion@HOSTEL_UPLINK_01", ["EV-001"])])
    assert c.required[0].state != "pass"
    assert timedelta(minutes=2) == obs[2].observed_at - obs[1].observed_at
