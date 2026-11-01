"""Connectivity logic (C3): blast radius, shared upstream, DNS/DHCP, reachability, peer consumption."""
from __future__ import annotations

from typing import Any, Optional

from connectivity_service.logic import Topology, handle_bundle
from netcoop.agents.base import BaseAgent, IncidentContext
from netcoop.agents.infrastructure import InfrastructureAgent
from netcoop.normalizer.result import NormalizationResult
from netcoop.orchestrator.run import Components, run_incident
from netcoop.orchestrator.transport import local_transport
from netcoop.protocol.models import Envelope, MessageType, Observation, Report
from tests.evidence_builders import CLOCK, TOPOLOGY, context, observations, signatures

TOPO = Topology(TOPOLOGY)
HOSTEL = ["HOSTEL_AP_01", "SW_CAMPUS_1", "SW_CAMPUS_3", "SW_CAMPUS_4", "SW_CAMPUS_5", "SW_CAMPUS_6"]


def _agent(agent_id: str, abbrev: str) -> BaseAgent:
    agent = BaseAgent()
    agent.agent_id, agent.abbrev = agent_id, abbrev
    return agent


def task(ctx: IncidentContext, peers: list[Envelope]) -> Envelope:
    orch = _agent("orchestrator", "ORCH")
    orch._seq = 2
    scope = sorted({t for o in ctx.observations for t in (o.device_id, o.link_id) if t})
    return orch.make(ctx, "connectivity_agent", MessageType.TASK,
                     {"task_id": f"{ctx.incident_id}-T-CONNECTIVITY", "capability": "interpret_peer_findings",
                      "scope_ids": scope, "peer_message_ids": [p.message_id for p in peers]}, None)


def peer(ctx: IncidentContext, sender: str, hypothesis: str, evidence: list[str], stance: str = "supports",
         target: Optional[str] = None) -> Envelope:
    agents = ctx.__dict__.setdefault("_peer_agents", {})     # one agent per sender, so IDs keep counting
    abbrev = {"performance_agent": "PERF", "infrastructure_agent": "INF"}[sender]
    return agents.setdefault(sender, _agent(sender, abbrev)).finding(ctx, "correlation_agent", f"{ctx.incident_id}-ORCH-01",
                                          hypothesis=hypothesis, stance=stance, target_hypothesis=target,
                                          evidence_ids=evidence, reason="peer test finding")


def run(obs: list[Observation], peers_of=lambda ctx: []) -> tuple[list[dict[str, Any]], list[Envelope]]:
    ctx = context(obs)
    peers = peers_of(ctx)
    out = handle_bundle([task(ctx, peers), *peers], obs, TOPOLOGY)
    for m in out:
        Envelope.model_validate(m.model_dump(mode="json"))
        assert m.sender == "connectivity_agent" and m.in_reply_to == f"{ctx.incident_id}-ORCH-03"
        if m.type == MessageType.FINDING.value:
            assert all(ctx.evidence(e) for e in m.payload["evidence_ids"])
    return [m.payload for m in out if m.type == MessageType.FINDING.value], out


def hyps(payloads: list[dict[str, Any]], stance: str = "supports") -> list[str]:
    return [p["hypothesis"] for p in payloads if p["stance"] == stance]


CONGESTION = [("HOSTEL_UPLINK_01", "link_utilization_pct", [92, 95, 96, 94, 97]),
              ("SW_CAMPUS_3", "latency_ms", [180, 220, 240, 230, 250]),
              ("SW_CAMPUS_3", "dns_success", [True] * 5)]


def congestion_peers(ctx: IncidentContext) -> list[Envelope]:
    return [peer(ctx, "performance_agent", "link_congestion@HOSTEL_UPLINK_01", ["EV-001", "EV-006"]),
            peer(ctx, "infrastructure_agent", "link_congestion@HOSTEL_UPLINK_01", ["EV-002"])]


# --------------------------------------------------------------------------- topology


def test_topology_upstream_and_blast_radius() -> None:
    assert TOPO.upstream("HOSTEL_AP_01") == ["HOSTEL_AP_LINK_01", "ACC_LINK_03", "HOSTEL_UPLINK_01"]
    assert TOPO.upstream("HOSTEL_UPLINK_01") == ["HOSTEL_UPLINK_01"]
    assert TOPO.upstream("CORE_RTR_01") == []
    assert TOPO.behind("HOSTEL_UPLINK_01") == HOSTEL
    assert TOPO.common_upstream(["SW_CAMPUS_3", "SW_CAMPUS_4"]) == "HOSTEL_UPLINK_01"
    assert TOPO.common_upstream(["HOSTEL_AP_01", "SW_CAMPUS_3"]) == "ACC_LINK_03"
    assert TOPO.common_upstream(["SW_CAMPUS_3", "SW_CAMPUS_7"]) is None
    assert TOPO.servers("dns") == ["SRV_DNS_DHCP_01"]


# --------------------------------------------------------------------------- peers change the conclusion


def test_peer_findings_localise_to_the_shared_uplink() -> None:
    payloads, _ = run(observations(CONGESTION), congestion_peers)
    [up] = [p for p in payloads if p["hypothesis"] == "upstream_dependency@HOSTEL_UPLINK_01"]
    assert up["consumed_message_ids"] == ["INC-0101-INF-01", "INC-0101-PERF-01"]
    assert up["score"] == 0.7                          # two independent agents agree
    assert all(d in up["reason"] for d in HOSTEL)      # blast radius is named
    assert set(up["evidence_ids"]) >= {"EV-001", "EV-002", "EV-006"}
    assert "healthy@HOSTEL_UPLINK_01" not in hyps(payloads)


def test_control_run_without_peers_gives_a_different_conclusion() -> None:
    normal, _ = run(observations(CONGESTION), congestion_peers)
    control, _ = run(observations(CONGESTION))
    assert hyps(control) == ["healthy@HOSTEL_UPLINK_01"]
    assert all(p["consumed_message_ids"] == [] for p in control)
    assert set(hyps(normal)) != set(hyps(control))
    # the DNS view is the same either way: DNS succeeds, so it argues against dns_failure
    assert hyps(normal, "contradicts") == hyps(control, "contradicts") == ["healthy@SRV_DNS_DHCP_01"]


def test_faults_on_two_access_switches_share_the_hostel_uplink() -> None:
    obs = observations([("SW_CAMPUS_3", "interface_errors", [0, 50, 90]),
                        ("SW_CAMPUS_4", "cpu_pct", [95, 96, 97])])
    payloads, _ = run(obs, lambda ctx: [
        peer(ctx, "infrastructure_agent", "interface_fault@SW_CAMPUS_3", ["EV-002"]),
        peer(ctx, "infrastructure_agent", "device_overload@SW_CAMPUS_4", ["EV-005"])])
    [up] = payloads
    assert up["hypothesis"] == "upstream_dependency@HOSTEL_UPLINK_01" and up["score"] == 0.5   # one agent only


def test_faults_with_no_shared_upstream_are_independent() -> None:
    obs = observations([("SW_CAMPUS_3", "interface_errors", [0, 50, 90]),
                        ("SW_CAMPUS_7", "interface_errors", [0, 40, 80])])
    payloads, _ = run(obs, lambda ctx: [
        peer(ctx, "infrastructure_agent", "interface_fault@SW_CAMPUS_3", ["EV-002"]),
        peer(ctx, "infrastructure_agent", "interface_fault@SW_CAMPUS_7", ["EV-005"])])
    assert hyps(payloads) == ["upstream_dependency@ACC_LINK_03", "upstream_dependency@ACC_LINK_07"]
    assert all(p["score"] == 0.4 and "independent" in p["reason"] for p in payloads)
    assert [p["consumed_message_ids"] for p in payloads] == [["INC-0101-INF-01"], ["INC-0101-INF-02"]]


def test_contradicting_peer_still_localises_its_own_cause() -> None:
    obs = observations([("DEPT_UPLINK_01", "interface_errors", [0, 50, 90]),
                        ("DEPT_UPLINK_01", "link_utilization_pct", [93, 95, 96])])
    payloads, _ = run(obs, lambda ctx: [peer(ctx, "infrastructure_agent", "interface_fault@DEPT_UPLINK_01",
                                             ["EV-002", "EV-005"], "contradicts",
                                             "link_congestion@DEPT_UPLINK_01")])
    assert hyps(payloads) == ["upstream_dependency@DEPT_UPLINK_01"]


def test_not_x_peers_localise_nothing() -> None:
    """"X contradicts X" only means "not X" (PROTOCOL.md "Reading stance"); on a healthy
    network the Performance agent sends these for every link, and they are not faults."""
    obs = observations([("HOSTEL_UPLINK_01", "link_utilization_pct", [31, 35, 33, 30, 34]),
                        ("DEPT_UPLINK_01", "link_utilization_pct", [28, 30, 29, 31, 27]),
                        ("SW_CAMPUS_3", "dns_success", [True] * 5)])
    payloads, _ = run(obs, lambda ctx: [
        peer(ctx, "performance_agent", "link_congestion@HOSTEL_UPLINK_01", ["EV-001"], "contradicts"),
        peer(ctx, "performance_agent", "link_congestion@DEPT_UPLINK_01", ["EV-006"], "contradicts")])
    assert not any(h.startswith("upstream_dependency") for h in hyps(payloads))
    assert all(not p["consumed_message_ids"] for p in payloads)


def test_healthy_or_unknown_peers_localise_nothing() -> None:
    obs = observations(CONGESTION)
    payloads, _ = run(obs, lambda ctx: [
        peer(ctx, "infrastructure_agent", "healthy@SW_CAMPUS_1", ["EV-001"], "contradicts",
             "interface_fault@SW_CAMPUS_1"),
        peer(ctx, "performance_agent", "link_congestion@NOT_IN_TOPOLOGY", ["EV-001"])])
    assert hyps(payloads) == ["healthy@HOSTEL_UPLINK_01"]


def test_peer_evidence_outside_the_bundle_is_not_cited() -> None:
    obs = observations(CONGESTION)
    ctx = context(obs)
    peers = congestion_peers(ctx)
    peers[0].payload["evidence_ids"] = ["EV-001", "EV-999"]      # cites evidence Connectivity was not sent
    out = handle_bundle([task(ctx, peers), *peers], obs, TOPOLOGY)
    assert all("EV-999" not in m.payload["evidence_ids"] for m in out)


# --------------------------------------------------------------------------- own checks


def test_dns_failure_on_two_clients_with_server_reachable() -> None:
    obs = observations([("SW_CAMPUS_3", "dns_success", [True, False, False]),
                        ("SW_CAMPUS_8", "dns_success", [False, False, False]),
                        ("SRV_DNS_DHCP_01", "reachability", [True, True, True])])
    payloads, _ = run(obs)
    [dns] = payloads
    assert dns["hypothesis"] == "dns_failure@SRV_DNS_DHCP_01" and dns["score"] == 0.8
    assert dns["evidence_ids"] == ["EV-003", "EV-006", "EV-009"]


def test_dns_failure_with_unreachable_server_points_at_the_path() -> None:
    obs = observations([("SW_CAMPUS_3", "dns_success", [False] * 3),
                        ("SRV_DNS_DHCP_01", "reachability", [True, False, False])])
    payloads, _ = run(obs)
    [p] = [p for p in payloads if p["stance"] == "contradicts"]
    assert p["target_hypothesis"] == "dns_failure@SRV_DNS_DHCP_01"
    assert p["hypothesis"] == "upstream_dependency@SRV_LINK_01"
    assert hyps(payloads) == ["upstream_dependency@SRV_LINK_01"]       # the reachability check agrees


def test_dhcp_failure_on_one_client_with_unknown_server_is_weaker() -> None:
    payloads, _ = run(observations([("SW_CAMPUS_9", "dhcp_success", [False] * 3)]))
    [dhcp] = payloads
    assert dhcp["hypothesis"] == "dhcp_failure@SRV_DNS_DHCP_01" and dhcp["score"] == 0.6
    assert "unknown reachability" in dhcp["reason"]


def test_unreachable_devices_behind_a_reachable_switch_blame_that_switch() -> None:
    obs = observations([("SW_CAMPUS_3", "reachability", [False] * 3), ("SW_CAMPUS_4", "reachability", [False] * 3),
                        ("SW_CAMPUS_1", "reachability", [True] * 3)])
    payloads, _ = run(obs)
    assert hyps(payloads) == ["upstream_dependency@SW_CAMPUS_1"]


def test_unreachable_distribution_switch_blames_its_uplink() -> None:
    obs = observations([("SW_CAMPUS_1", "reachability", [False] * 3), ("SW_CAMPUS_3", "reachability", [False] * 3)])
    payloads, _ = run(obs)
    [up] = payloads
    assert up["hypothesis"] == "upstream_dependency@HOSTEL_UPLINK_01" and up["score"] == 0.8


def test_missing_and_stale_checks_are_unknown_not_failures() -> None:
    obs = observations([("SW_CAMPUS_3", "reachability", [None, False])],
                       flags={("reachability", 0): ["missing"], ("reachability", 1): ["stale"]})
    payloads, out = run(obs)
    assert payloads == []
    assert out[0].type == MessageType.RESPONSE.value and "cannot judge" in out[0].payload["answer"]


def test_string_check_values_are_understood() -> None:
    payloads, _ = run(observations([("SW_CAMPUS_3", "dns_success", ["fail", "fail"]),
                                    ("SW_CAMPUS_4", "dns_success", ["false", "timeout"])]))
    assert hyps(payloads) == ["dns_failure@SRV_DNS_DHCP_01"]


def test_deterministic() -> None:
    a, _ = run(observations(CONGESTION), congestion_peers)
    b, _ = run(observations(CONGESTION), congestion_peers)
    assert a == b and [p["finding_id"] for p in a] == ["F-CONN-01", "F-CONN-02"]


# --------------------------------------------------------------------------- end to end with the real agents


def test_pipeline_with_real_infrastructure_and_connectivity() -> None:
    obs = observations([("SW_CAMPUS_1", "queue_drops", [1200, 1600, 2100, 2700, 3300]),
                        ("HOSTEL_UPLINK_01", "link_utilization_pct", [92, 95, 96, 94, 97]),
                        ("SW_CAMPUS_3", "dns_success", [True] * 5)])

    def normalize(*_: Any) -> NormalizationResult:
        return NormalizationResult(observations=obs, normalization_log=[], window_end=CLOCK)

    comps = Components(normalize=normalize, agents={"infrastructure_agent": InfrastructureAgent()},
                       connectivity=local_transport(handle_bundle))
    fixture = {"incident_id": "INC-0101", "records": [{}]}

    def conn(control: bool) -> list[dict[str, Any]]:
        comps.agents["infrastructure_agent"] = InfrastructureAgent()
        result = run_incident(fixture, TOPOLOGY, signatures(), components=comps, control=control)
        Report.model_validate(result.report.model_dump(mode="json"))
        return [m.payload for m in result.findings if m.sender == "connectivity_agent"]

    normal, control = conn(False), conn(True)
    assert "upstream_dependency@HOSTEL_UPLINK_01" in hyps(normal)
    assert any(p["consumed_message_ids"] for p in normal)
    assert hyps(control) == ["healthy@HOSTEL_UPLINK_01"]
