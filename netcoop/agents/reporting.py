"""Reporting agent: turn Correlation's DECISION into the report-1.0 JSON and a Markdown view.

It only uses what Correlation decided and the evidence its checks used: it never adds a
conclusion. Every cited evidence ID must exist in the incident and every affected ID must
be in the topology; anything that does not is dropped and named in `limitations`.
"""
from __future__ import annotations

from typing import Any, Optional

from netcoop.agents.base import BaseAgent, IncidentContext
from netcoop.agents.correlation import Candidate, _Topo
from netcoop.protocol.models import Envelope, EvidenceItem, Report, Status

# Practical, read-only-first actions per cause; {t} is the target, {d} the affected device.
ACTIONS: dict[str, list[str]] = {
    "link_congestion": ["Check egress queue depth and top talkers on {t}",
                        "Rebalance or rate-limit bulk traffic, or add capacity to {t} if utilization stays high",
                        "Confirm upstream links of {t} are not saturated too"],
    "interface_fault": ["Inspect CRC/alignment counters and transceiver/cable on {t}",
                        "Reseat or replace the cable/optic on {t}; move the port if errors continue",
                        "Watch interface_errors deltas for 10 minutes after the change"],
    "weak_wifi": ["Check client RSSI/SNR and channel utilization on {d}",
                  "Adjust AP transmit power/channel or add coverage near {d}",
                  "Keep the AP uplink under watch: it is healthy now, so the problem is the radio side"],
    "device_overload": ["Identify the process or traffic driving CPU/memory on {d}",
                        "Move control-plane load off {d} or schedule a maintenance restart"],
    "hardware_failure": ["Check power, interface and module status on {d} and its logs",
                         "Fail over to redundant hardware and open a replacement ticket for {d}"],
    "dns_failure": ["Check the DNS service on {d} (process, zone loading, logs)",
                    "Test resolution from a client against {d} and against a secondary resolver",
                    "Fail clients over to the secondary resolver until {d} answers"],
    "dhcp_failure": ["Check the DHCP service and lease pool on {d}",
                     "Verify relay (ip helper) configuration on the access switches",
                     "Extend or free the lease pool if it is exhausted"],
    "upstream_dependency": ["Check the shared upstream {t} and the device just below it"],
}


class ReportingAgent(BaseAgent):
    agent_id = "reporting_agent"
    abbrev = "REP"

    def build(self, ctx: IncidentContext, decision: dict[str, Any], findings: list[Envelope],
              version: str, run_limitations: list[str], validation_errors: list[str]) -> Report:
        status: Status = decision["status"]
        leader: Optional[Candidate] = decision["leader"]
        ranked: list[Candidate] = decision["ranked"]
        topo = _Topo(ctx.topology)
        limitations = list(decision["limitations"]) + list(run_limitations)
        named = status in (Status.diagnosed, Status.probable)

        cause = leader.hypothesis if named and leader else None
        if status == Status.healthy:
            confidence = decision["healthy_confidence"]
        else:
            confidence = leader.confidence if leader else 0.0

        devices: list[str] = []
        segments: list[str] = []
        evidence: list[EvidenceItem] = []
        if named and leader:
            devices, segments = topo.affected(leader.target)
            evidence = self._evidence(ctx, leader.passing_evidence(), limitations)
        elif status == Status.healthy:
            evidence = self._evidence(ctx, _healthy_samples(ctx), limitations)
        elif leader:
            evidence = self._evidence(ctx, leader.passing_evidence(), limitations)
        unknown_ids = [i for i in devices + segments if not topo.knows(i)]
        if unknown_ids:
            limitations.append(f"affected IDs not in topology dropped: {unknown_ids}")
            devices = [d for d in devices if topo.knows(d)]
            segments = [s for s in segments if topo.knows(s)]

        # Every nominated candidate that the evidence did not refute (Phase 3 review R1).
        rivals = list(decision.get("alternatives", []))
        alternatives = [c.hypothesis for c in rivals]
        if status == Status.diagnosed and leader:
            actions = _actions(leader, topo)
        elif status == Status.probable and leader:
            actions = [f"Confirm before acting: {a}" for a in _actions(leader, topo)[:2]]
            actions += [f"Rule out {c.hypothesis}: {(_actions(c, topo) or ['re-check'])[0]}" for c in rivals[:1]]
        elif status == Status.healthy:
            actions = ["No action needed: measurements are normal and service/path checks pass",
                       "If users still report problems, collect a client-side capture with timestamps"]
        else:
            actions = _next_checks(leader)
        for c in decision.get("unresolved", []):
            actions.append(f"Collect the evidence needed to assess {c.hypothesis}: {'; '.join(c.unknown_reasons())}")

        sources = sorted({f.sender for f in findings if f.sender.endswith("_agent")} |
                         ({"correlation_agent"} if ranked else set()))
        return Report(
            incident_id=ctx.incident_id, status=status, suspected_root_cause=cause, confidence=confidence,
            confidence_method="heuristic-v1", signature_version=version,
            affected_devices=devices, affected_segments=segments, supporting_evidence=evidence,
            agent_findings=sorted({f.payload["finding_id"] for f in findings}),
            alternative_causes=alternatives, recommended_actions=actions, evidence_sources=sources,
            limitations=_unique(limitations), normalization_log=ctx.normalization_log,
            validation_errors=validation_errors, agent_trace_id=f"{ctx.incident_id}-TRACE")

    @staticmethod
    def _evidence(ctx: IncidentContext, observations: list[Any], limitations: list[str]) -> list[EvidenceItem]:
        """Cited evidence, at most 3 samples per (target, metric, source) to keep the report readable."""
        items: list[EvidenceItem] = []
        per_series: dict[tuple[Any, ...], int] = {}
        for o in observations:
            if ctx.evidence(o.evidence_id) is None:
                limitations.append(f"evidence {o.evidence_id} not in the incident; not cited")
                continue
            key = (o.device_id or o.link_id, o.metric, o.source_id)
            per_series[key] = per_series.get(key, 0) + 1
            if per_series[key] > 3:
                continue
            items.append(EvidenceItem(evidence_id=o.evidence_id, metric=o.metric, value=o.value, unit=o.unit,
                                      source_id=o.source_id))
        return items


def _actions(c: Candidate, topo: _Topo) -> list[str]:
    devices, _ = topo.affected(c.target)
    return [a.format(t=c.target, d=devices[0] if devices else c.target) for a in ACTIONS.get(c.cause, [])]


def _next_checks(leader: Optional[Candidate]) -> list[str]:
    if leader is None:
        return ["Collect fresh performance, infrastructure and service/path measurements for the affected area, "
                "then re-run the diagnosis"]
    missing = [c.name for c in leader.required if c.state != "pass"]
    if not leader.corroboration_met:
        return [f"Collect corroborating evidence for {leader.hypothesis}: {leader.corroboration_need} "
                f"(high utilization alone is a busy link, not a fault)",
                "Re-run the diagnosis on a fresh 5-minute window before changing anything"]
    return [f"Collect the missing check(s) for {leader.hypothesis}: {', '.join(missing) or 'more samples'}",
            "Re-run the diagnosis on a fresh 5-minute window before changing anything"]


def _healthy_samples(ctx: IncidentContext) -> list[Any]:
    latest: dict[tuple[Any, ...], Any] = {}
    for o in sorted(ctx.observations, key=lambda o: (o.observed_at, o.evidence_id)):
        if o.value is not None and o.metric in ("latency_ms", "link_utilization_pct", "reachability", "dns_success"):
            latest[(o.device_id or o.link_id, o.metric, o.source_id)] = o
    return sorted(latest.values(), key=lambda o: o.evidence_id)[:8]


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    return [i for i in items if not (i in seen or seen.add(i))]


def to_markdown(report: Report) -> str:
    """Human-readable view of the same report (no extra conclusions)."""
    r = report
    lines = [f"# Incident {r.incident_id}: {r.status}", ""]
    if r.suspected_root_cause:
        lines.append(f"**Suspected root cause:** `{r.suspected_root_cause}`  ")
    lines.append(f"**Confidence:** {r.confidence if r.confidence is not None else 'n/a'} ({r.confidence_method}, "
                 f"signatures {r.signature_version})")
    if r.affected_devices or r.affected_segments:
        lines.append(f"**Affected:** {', '.join(r.affected_segments + r.affected_devices)}")
    if r.supporting_evidence:
        lines += ["", "## Evidence", "", "| ID | metric | value | source |", "|---|---|---|---|"]
        lines += [f"| {e.evidence_id} | {e.metric} | {e.value} {e.unit} | {e.source_id} |" for e in r.supporting_evidence]
    for title, items in (("Alternative causes", r.alternative_causes), ("Recommended actions", r.recommended_actions),
                         ("Limitations", r.limitations), ("Validation errors", r.validation_errors),
                         ("Normalization", r.normalization_log[:10])):
        if items:
            lines += ["", f"## {title}", ""] + [f"- {i}" for i in items]
    lines += ["", f"Findings: {', '.join(r.agent_findings) or 'none'} · trace: {r.agent_trace_id}", ""]
    return "\n".join(lines)
