"""Reporting agent: turn Correlation's DECISION into the report-1.0 JSON and a Markdown view.

It only uses what Correlation decided and the evidence its checks used: it never adds a
conclusion. Every cited evidence ID must exist in the incident and every affected ID must
be in the topology; anything that does not is dropped and named in `limitations`.
"""
from __future__ import annotations

from typing import Any, Optional

from netcoop.agents.base import BaseAgent, IncidentContext
from netcoop.agents.correlation import Candidate, _Topo
from netcoop.normalizer.normalize import COUNTERS
from netcoop.protocol.models import Envelope, EvidenceItem, Observation, Report, Status

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

CAUSE_TEXT = {"link_congestion": "link congestion", "weak_wifi": "a weak Wi-Fi signal",
              "interface_fault": "an Ethernet/interface fault", "device_overload": "device overload",
              "dns_failure": "a DNS service failure", "dhcp_failure": "a DHCP service failure",
              "hardware_failure": "a hardware failure", "upstream_dependency": "an upstream dependency failure"}

# Reported confidence is the heuristic-v1 score capped by status, so the number never says more than
# the status does: nothing from one window is certain, and a cause that missed the diagnosed gate
# (0.70) never outscores one that passed it. The uncapped score stays in the DECISION ranking.
CONFIDENCE_CAP = {Status.diagnosed: 0.95, Status.healthy: 0.95, Status.probable: 0.69,
                  Status.insufficient_evidence: 0.49}
EXCLUDED_FLAGS = ("malformed", "missing", "stale", "out_of_window")


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
        cap = CONFIDENCE_CAP.get(status)
        if cap is not None and confidence > cap:
            why = ("a cause that missed the diagnosed gate is reported below 0.70" if status == Status.probable
                   else "an abstention is reported below the 0.50 probable gate"
                   if status == Status.insufficient_evidence
                   else "no conclusion from one telemetry window is reported as certain")
            limitations.insert(0, f"heuristic-v1 score {confidence:.2f} reported as {cap:.2f}: {why}")
            confidence = cap
        limitations[0:0] = _data_quality(ctx)
        if status in (Status.diagnosed, Status.probable, Status.healthy):
            limitations.insert(0, f"based on the {ctx.signatures.get('window', {}).get('minutes', 5)}-minute window "
                                  f"ending {ctx.clock.isoformat()}; confidence is the documented heuristic-v1 score, "
                                  "not a calibrated probability")

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
            delta = _interval_delta(ctx, o)
            items.append(EvidenceItem(evidence_id=o.evidence_id, metric=o.metric, value=o.value, unit=o.unit,
                                      source_id=o.source_id, interval_delta=delta,
                                      interpretation=_interpret(ctx.threshold(o.metric), o, delta)))
        return items


def _interval_delta(ctx: IncidentContext, o: Observation) -> Optional[float]:
    """A cumulative counter means little alone (Phase 2 review P2-1): the increase since the previous
    sample of the same target, metric and source. None after a gap or on a reset, never a negative number."""
    if o.metric not in COUNTERS or not isinstance(o.value, (int, float)) or isinstance(o.value, bool) \
            or "counter_reset" in o.quality_flags:
        return None
    target = o.device_id or o.link_id
    earlier = sorted((x for x in ctx.observations if x.metric == o.metric and x.source_id == o.source_id
                      and (x.device_id or x.link_id) == target and x.observed_at < o.observed_at),
                     key=lambda x: (x.observed_at, x.evidence_id))
    if not earlier or not isinstance(earlier[-1].value, (int, float)) or isinstance(earlier[-1].value, bool):
        return None
    delta = o.value - earlier[-1].value
    return round(delta, 3) if delta >= 0 else None


def _interpret(th: dict[str, Any], o: Observation, delta: Optional[float]) -> str:
    """The value against its configured threshold (signatures-1.0.yaml), in words."""
    v, warn, crit, direction = o.value, th.get("warn"), th.get("critical"), th.get("direction")
    if v is None:
        return "missing or malformed; not used"
    if isinstance(v, bool):
        return f"{o.metric} check {'passed' if v else 'FAILED'}"
    if isinstance(v, str):
        return f"{o.metric} is {v}"
    if direction == "rising_delta":
        if delta is None:
            return "cumulative counter; no per-interval increase (first sample, gap or reset)"
        if delta >= crit:
            return f"+{delta:g} in one interval, at or above critical {crit:g}"
        if delta >= warn:
            return f"+{delta:g} in one interval, at or above warn {warn:g}"
        return f"+{delta:g} in one interval, below warn {warn:g}"
    if direction == "high":
        if v >= crit:
            return f"above critical {crit:g} {o.unit}"
        return f"above warn {warn:g} {o.unit}" if v >= warn else f"normal (below warn {warn:g} {o.unit})"
    if direction == "low":
        if v <= crit:
            return f"at or below critical {crit:g} {o.unit}"
        return f"at or below warn {warn:g} {o.unit}" if v <= warn else f"normal (above warn {warn:g} {o.unit})"
    return "context value; not judged against a threshold"


def _data_quality(ctx: IncidentContext) -> list[str]:
    """Readings the normalizer excluded, so a report never hides that part of its input was unusable."""
    out = []
    for flag in EXCLUDED_FLAGS:
        hit = [o for o in ctx.observations if flag in o.quality_flags]
        if hit:
            metrics = sorted({o.metric for o in hit})
            out.append(f"data quality: {len(hit)} {flag} reading(s) excluded from the checks "
                       f"({', '.join(metrics[:5])}{', ...' if len(metrics) > 5 else ''})")
    return out


def summarize(r: Report) -> str:
    """One plain-language sentence for the report's outcome (no conclusion beyond the status)."""
    cause, _, target = (r.suspected_root_cause or "").partition("@")
    text = f"{CAUSE_TEXT.get(cause, cause)} on {target}"
    where = f", affecting {', '.join(r.affected_devices)}" if r.affected_devices else ""
    rejected = (f" {len(r.validation_errors)} input problem(s) were rejected and are listed in validation_errors."
                if r.validation_errors else "")
    if r.status == Status.diagnosed:
        return (f"Diagnosed {text}{where}: {len(r.supporting_evidence)} evidence items from "
                f"{len({e.source_id for e in r.supporting_evidence})} independent sources agree "
                f"(confidence {r.confidence}).{rejected}")
    if r.status == Status.probable:
        rival = (f"{r.alternative_causes[0]} cannot yet be ruled out" if r.alternative_causes
                 else "a required check is still missing")
        return f"Probably {text}{where}, but not confirmed: {rival}. Verify before acting.{rejected}"
    if r.status == Status.healthy:
        return f"No fault found: fresh measurements are normal and service/path checks pass.{rejected}"
    if r.status == Status.insufficient_evidence:
        return f"No diagnosis: the evidence is not enough to name a cause without guessing.{rejected}"
    if r.status == Status.invalid_input:
        return f"Input rejected before analysis: {r.validation_errors[0] if r.validation_errors else 'invalid'}."
    return f"The run failed: {r.limitations[0] if r.limitations else 'see limitations'}."


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


def _delta_text(e: EvidenceItem) -> str:
    if e.metric not in COUNTERS:
        return ""
    return f"+{e.interval_delta:g}" if e.interval_delta is not None else "n/a (first sample, gap or reset)"


def to_markdown(report: Report) -> str:
    """Human-readable view of the same report (no extra conclusions)."""
    r = report
    lines = [f"# Incident {r.incident_id}: {r.status}", ""]
    if r.summary:
        lines += [f"> {r.summary}", ""]
    if r.suspected_root_cause:
        lines.append(f"**Suspected root cause:** `{r.suspected_root_cause}`  ")
    lines.append(f"**Confidence:** {r.confidence if r.confidence is not None else 'n/a'} ({r.confidence_method}, "
                 f"signatures {r.signature_version})")
    if r.affected_devices or r.affected_segments:
        lines.append(f"**Affected:** {', '.join(r.affected_segments + r.affected_devices)}")
    if r.supporting_evidence:
        lines += ["", "## Evidence", "", "| ID | metric | value | per interval | meaning | source |",
                  "|---|---|---|---|---|---|"]
        lines += [f"| {e.evidence_id} | {e.metric} | {e.value} {e.unit} | {_delta_text(e)} | {e.interpretation or ''} "
                  f"| {e.source_id} |"
                  for e in r.supporting_evidence]
    for title, items in (("Alternative causes", r.alternative_causes), ("Recommended actions", r.recommended_actions),
                         ("Limitations", r.limitations), ("Validation errors", r.validation_errors),
                         ("Normalization", r.normalization_log[:10])):
        if items:
            lines += ["", f"## {title}", ""] + [f"- {i}" for i in items]
    lines += ["", f"Findings: {', '.join(r.agent_findings) or 'none'} · trace: {r.agent_trace_id}", ""]
    return "\n".join(lines)
