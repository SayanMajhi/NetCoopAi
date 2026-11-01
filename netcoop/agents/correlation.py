"""Correlation agent: rank fault signatures, verify with QUERY, apply heuristic-v1 and the gates.

Agents *nominate* hypotheses (FINDINGs that name a fault, PROTOCOL.md "Reading stance").
Correlation does not trust their verdicts: for every candidate it evaluates the signature
in netcoop/config/signatures-1.0.yaml itself, check by check, on the canonical evidence,
with the frozen check vocabulary and scopes. A measurement counts once; agent count adds
nothing.

heuristic-v1 (ARCHITECTURE.md §4), every factor in [0, 1]:

    raw = clamp(0.40*signal_fit + 0.25*topology_fit + 0.20*time_fit
                + 0.15*independent_source_fit - 0.15*contradiction_strength, 0, 1)
    confidence = round(raw * coverage, 2)

- signal_fit = 0.6*(required checks passing / required) + 0.2*corroboration + 0.2*severity.
  corroboration = matches / min_matches (capped at 1); with min_matches 0 it is 1 if any
  corroborating check passes, else 0.5. severity is 0.5 until a QUERY verifies the key
  measurement (critical level -> high, only warn level -> low).
- coverage = required checks with fresh, valid evidence / required checks.
- topology_fit = 1 if the Connectivity agent scoped the same hypothesis (or the target sits
  behind a shared upstream it named), 0.5 if the target is only known to the topology, else 0.
- time_fit = fresh evidence / (fresh + stale evidence) for the metrics the checks use.
- independent_source_fit = 1 for >= 2 distinct raw sources behind the passing checks,
  0.5 for >= 2 metrics from one source, else 0.
- contradiction_strength = 1 if a signature contradicting check passes, else the highest
  score of a contradicts FINDING aimed at the hypothesis (0.5 when it has no score), else 0.

Gates: diagnosed = all required checks pass, confidence >= 0.70, coverage >= 0.75,
independent_source_fit == 1 and a lead >= 0.15 over the runner-up; probable = confidence
>= 0.50 with the reason it is not diagnosed named; healthy = no candidate passes all its
required checks, and fresh normal performance + infrastructure measurements, passing
service checks and passing path checks, with no missing/stale data; else
insufficient_evidence.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import timedelta
from statistics import median
from typing import Any, Iterable, Optional

from netcoop.agents.base import BaseAgent, IncidentContext
from netcoop.protocol.cards import CARDS
from netcoop.protocol.models import (
    DecisionPayload,
    Envelope,
    MessageType,
    Observation,
    QueryPayload,
    RankedCause,
    Stance,
    Status,
)

UNUSABLE = {"missing", "stale", "out_of_window", "malformed"}
INCOMPLETE = {"missing", "stale", "out_of_window"}
PERFORMANCE_METRICS = {"latency_ms", "jitter_ms", "packet_loss_pct", "link_utilization_pct", "throughput_mbps"}
INFRA_METRICS = {"cpu_pct", "memory_pct", "queue_drops", "interface_errors", "link_flap_count", "rssi_dbm",
                 "retry_rate_pct", "interface_oper_status"}
# The measurement a QUERY verifies for each cause (owned by an in-process agent).
KEY_MEASUREMENT = {"link_congestion": "link_utilization_pct", "interface_fault": "interface_errors",
                   "weak_wifi": "rssi_dbm", "device_overload": "cpu_pct", "hardware_failure": "interface_oper_status"}
MAX_QUERY_ROUNDS = 2
CLOSE = 0.15


@dataclass
class Check:
    """One signature check evaluated on evidence: pass / fail / unknown (no usable evidence) /
    inconclusive (evidence present but it neither confirms nor refutes, e.g. utilization that keeps
    returning above warn without a sustained run above critical)."""

    name: str
    state: str
    evidence: list[Observation] = field(default_factory=list)
    detail: str = ""          # why a check is unknown, e.g. "2 of 3 required fresh samples"


@dataclass
class Candidate:
    hypothesis: str
    supporting: list[Envelope] = field(default_factory=list)
    contradicting: list[Envelope] = field(default_factory=list)
    required: list[Check] = field(default_factory=list)
    corroborating: list[Check] = field(default_factory=list)
    contradicting_checks: list[Check] = field(default_factory=list)
    min_matches: int = 0
    corroboration_need: str = ""   # e.g. "queue_drops rising >= 10/interval or latency above 100 ms"
    severity: float = 0.5
    verified: Optional[str] = None
    factors: dict[str, float] = field(default_factory=dict)
    coverage: float = 0.0
    confidence: float = 0.0

    @property
    def cause(self) -> str:
        return self.hypothesis.split("@", 1)[0]

    @property
    def target(self) -> str:
        return self.hypothesis.split("@", 1)[1]

    @property
    def required_pass(self) -> bool:
        return bool(self.required) and all(c.state == "pass" for c in self.required)

    @property
    def corroboration_met(self) -> bool:
        """ARCHITECTURE 3: e.g. congestion needs high utilization *plus* queue-drop/latency corroboration."""
        return sum(c.state == "pass" for c in self.corroborating) >= self.min_matches

    @property
    def refuted(self) -> bool:
        """The evidence speaks against it: a required check fails or a contradicting check passes."""
        return any(c.state == "fail" for c in self.required) or any(c.state == "pass" for c in self.contradicting_checks)

    @property
    def unassessed(self) -> bool:
        """Not refuted, but too little fresh evidence to judge it (Phase 3 review R1)."""
        return not self.refuted and self.coverage < 0.75 and any(c.state == "unknown" for c in self.required)

    @property
    def unresolved(self) -> bool:
        """Neither confirmed nor refuted: unassessed, or a required check is inconclusive."""
        return not self.refuted and (self.unassessed or any(c.state == "inconclusive" for c in self.required))

    def unknown_reasons(self) -> list[str]:
        return [f"{c.name}: {c.detail or 'no fresh evidence'}" for c in self.required
                if c.state in ("unknown", "inconclusive")]

    def passing_evidence(self) -> list[Observation]:
        seen: dict[str, Observation] = {}
        for check in self.required + self.corroborating:
            if check.state == "pass":
                for o in check.evidence:
                    seen.setdefault(o.evidence_id, o)
        return sorted(seen.values(), key=lambda o: o.evidence_id)


# --------------------------------------------------------------------------- topology helpers


class _Topo:
    def __init__(self, topology: dict[str, Any]) -> None:
        self.links = {l["link_id"]: l for l in topology.get("links") or [] if isinstance(l, dict)}
        self.nodes = {n["id"]: n for n in topology.get("nodes") or [] if isinstance(n, dict)}
        self.parent_link = {l["b"]: lid for lid, l in self.links.items()}

    def knows(self, target: str) -> bool:
        return target in self.links or target in self.nodes

    def downstream(self, target: str) -> list[str]:
        """Devices behind a link, or a device's descendants (not the device itself)."""
        start = [self.links[target]["b"]] if target in self.links else \
            [l["b"] for l in self.links.values() if l["a"] == target]
        out: list[str] = []
        while start:
            node = start.pop()
            if node not in out:
                out.append(node)
                start.extend(l["b"] for l in self.links.values() if l["a"] == node)
        return sorted(out)

    def endpoints(self, target: str) -> list[str]:
        return [self.links[target]["b"], self.links[target]["a"]] if target in self.links else [target]

    def servers(self, service: str) -> list[str]:
        return sorted(n for n, node in self.nodes.items() if service in (node.get("services") or []))

    def affected(self, target: str) -> tuple[list[str], list[str]]:
        """(affected_devices, affected_segments) for a fault on `target`."""
        if target in self.links:
            return [self.links[target]["b"]], [target]
        return [target], []


# --------------------------------------------------------------------------- evidence helpers


def _num(o: Observation) -> Optional[float]:
    if isinstance(o.value, bool) or o.value is None:
        return None
    try:
        v = float(o.value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _bool(o: Observation) -> Optional[bool]:
    if isinstance(o.value, bool):
        return o.value
    text = str(o.value).strip().lower()
    return True if text in {"true", "1", "yes", "ok", "up", "success"} else \
        False if text in {"false", "0", "no", "fail", "failed", "down", "timeout"} else None


class _Evidence:
    """Fresh, usable observations of the incident window, by target and metric."""

    def __init__(self, ctx: IncidentContext) -> None:
        minutes = (ctx.signatures.get("window") or {}).get("minutes", 5)
        start = ctx.clock - timedelta(minutes=minutes)
        self.ctx = ctx
        self.fresh = [o for o in ctx.observations if o.value is not None and not o.source_id.startswith("on-demand")
                      and not UNUSABLE.intersection(o.quality_flags) and start <= o.observed_at <= ctx.clock]
        self.stale = [o for o in ctx.observations if INCOMPLETE.intersection(o.quality_flags)]

    def series(self, targets: Iterable[str], metric: str) -> dict[str, list[Observation]]:
        wanted = set(targets)
        out: dict[str, list[Observation]] = {}
        for o in self.fresh:
            t = o.device_id or o.link_id
            if t in wanted and o.metric == metric:
                estimator = "derived" if "derived" in o.quality_flags else "measured"
                out.setdefault(f"{t}|{o.source_id}|{estimator}", []).append(o)
        return {k: sorted(v, key=lambda o: (o.observed_at, o.evidence_id)) for k, v in sorted(out.items())}

    def stale_for(self, targets: Iterable[str], metric: str) -> int:
        wanted = set(targets)
        return sum(1 for o in self.stale if (o.device_id or o.link_id) in wanted and o.metric == metric)


# --------------------------------------------------------------------------- the agent


class CorrelationAgent(BaseAgent):
    agent_id = "correlation_agent"
    abbrev = "CORR"

    def __init__(self) -> None:
        super().__init__()
        self.candidates: dict[str, Candidate] = {}
        self.rounds = 0
        self.history: list[str] = []
        self._queried: dict[str, str] = {}      # query_id -> hypothesis

    # ----------------------------------------------------------------- ranking

    def rank(self, ctx: IncidentContext, findings: list[Envelope]) -> list[Candidate]:
        """Nominate candidates from FINDINGs, evaluate their signatures, score them."""
        sigs = ctx.signatures.get("signatures") or {}
        topo, ev = _Topo(ctx.topology), _Evidence(ctx)
        for f in findings:
            p = f.payload
            hypothesis, target_h = p["hypothesis"], p.get("target_hypothesis", p["hypothesis"])
            names_fault = p["stance"] == Stance.supports.value or hypothesis != target_h
            cause = hypothesis.split("@", 1)[0]
            if names_fault and cause in sigs and topo.knows(hypothesis.split("@", 1)[1]):
                self.candidates.setdefault(hypothesis, Candidate(hypothesis)).supporting.append(f)
        for f in findings:
            p = f.payload
            if p["stance"] == Stance.contradicts.value and p.get("target_hypothesis") in self.candidates:
                self.candidates[p["target_hypothesis"]].contradicting.append(f)
        for cand in self.candidates.values():
            self._evaluate(cand, sigs[cand.cause], ctx, topo, ev)
            self._score(cand, ctx, topo, ev, findings)
        return self.ranking()

    def ranking(self) -> list[Candidate]:
        """By confidence (reported to 2 decimals); a tie at that precision goes to the stronger
        signature evidence (signal_fit), then the name, never to the name alone."""
        return sorted(self.candidates.values(),
                      key=lambda c: (-c.confidence, -c.factors.get("signal_fit", 0.0), c.hypothesis))

    def _evaluate(self, cand: Candidate, sig: dict[str, Any], ctx: IncidentContext, topo: _Topo, ev: _Evidence) -> None:
        cand.required = [self._item(i, cand.target, ctx, topo, ev) for i in sig.get("required") or []]
        corr = sig.get("corroborating") or {}
        cand.min_matches = int(corr.get("min_matches") or 0)
        cand.corroborating = [self._item(i, cand.target, ctx, topo, ev) for i in corr.get("checks") or []]
        cand.corroboration_need = " or ".join(_describe(i, ctx) for i in corr.get("checks") or [])
        cand.contradicting_checks = [self._item(i, cand.target, ctx, topo, ev) for i in sig.get("contradicting") or []]

    def _item(self, item: dict[str, Any], target: str, ctx: IncidentContext, topo: _Topo, ev: _Evidence) -> Check:
        if "any_of" in item:
            parts = [self._check(i, target, ctx, topo, ev) for i in item["any_of"]]
            name = " or ".join(p.name for p in parts)
            passing = [p for p in parts if p.state == "pass"]
            if passing:
                return Check(name, "pass", [o for p in passing for o in p.evidence])
            if all(p.state == "fail" for p in parts):
                return Check(name, "fail", [o for p in parts for o in p.evidence])
            state = "inconclusive" if any(p.state == "inconclusive" for p in parts) else "unknown"
            return Check(name, state, detail="; ".join(p.detail for p in parts if p.detail))
        return self._check(item, target, ctx, topo, ev)

    def _locations(self, item: dict[str, Any], target: str, topo: _Topo) -> list[list[str]]:
        """Groups of targets to look at, in order; the first group with evidence is used."""
        scope = item.get("scope", "target")
        if scope == "ap_uplink":
            return [[topo.parent_link[target]]] if target in topo.parent_link else []
        if scope == "downstream":
            return [topo.downstream(target)]
        if scope in ("dns_server", "dhcp_server"):
            return [topo.servers(scope.split("_")[0])]
        if scope == "multiple_clients":
            return [sorted(set(topo.nodes) - set(topo.servers("dns")) - set(topo.servers("dhcp")))]
        if item["metric"] in ("dns_success", "dhcp_success"):   # service checks run on clients
            return [sorted(set(topo.nodes) - set(topo.servers("dns")) - set(topo.servers("dhcp")))]
        return [[target], topo.endpoints(target), topo.downstream(target)]

    def _check(self, item: dict[str, Any], target: str, ctx: IncidentContext, topo: _Topo, ev: _Evidence) -> Check:
        metric, check = item["metric"], item["check"]
        name = f"{metric} {check}" + (f" ({item['scope']})" if item.get("scope") else "")
        th = ctx.threshold(metric)
        for group in self._locations(item, target, topo):
            series = ev.series(group, metric)
            if series:
                state, used = _judge(check, metric, series, th, ctx)
                detail = ""
                if state in ("unknown", "inconclusive"):
                    need = ((ctx.signatures.get("window") or {}).get("congestion_min_consecutive_samples", 3)
                            if check == "sustained_above_critical" else 2 if check == "rising_delta" else 1)
                    have = max(len(s) for s in series.values())
                    if have < need:
                        detail = f"{metric} has {have} of {need} required fresh samples"
                    elif check == "sustained_above_critical" and th.get("critical") is not None:
                        detail = (f"{metric} stays above warn but shows no sustained run above critical "
                                  f"in {have} samples: inconclusive")
                    else:
                        detail = f"no threshold configured for {metric}"
                return Check(name, state, used, detail)
        return Check(name, "unknown", detail=f"no fresh {metric} samples in scope")

    def _score(self, cand: Candidate, ctx: IncidentContext, topo: _Topo, ev: _Evidence,
               findings: list[Envelope]) -> None:
        req = cand.required
        req_fit = sum(1.0 if c.state == "pass" else 0.5 if c.state == "inconclusive" else 0.0
                      for c in req) / len(req) if req else 0.0
        cand.coverage = sum(c.state != "unknown" for c in req) / len(req) if req else 0.0
        matches = sum(c.state == "pass" for c in cand.corroborating)
        if cand.min_matches:
            corr_fit = min(1.0, matches / cand.min_matches)
        else:
            corr_fit = 1.0 if matches else 0.5
        signal = 0.6 * req_fit + 0.2 * corr_fit + 0.2 * cand.severity

        scoped = any(f.sender == "connectivity_agent" and f.payload["stance"] == Stance.supports.value
                     and (f.payload["hypothesis"] == cand.hypothesis
                          or (f.payload["hypothesis"].startswith("upstream_dependency@")
                              and cand.target in topo.downstream(f.payload["hypothesis"].split("@", 1)[1])))
                     for f in findings)
        topology = 1.0 if scoped else (0.5 if topo.knows(cand.target) else 0.0)

        used = cand.passing_evidence()
        metrics = {o.metric for c in req + cand.corroborating for o in c.evidence}
        stale = sum(ev.stale_for([cand.target, *topo.endpoints(cand.target), *topo.downstream(cand.target)], m)
                    for m in metrics)
        time_fit = len(used) / (len(used) + stale) if used else 0.0

        sources = {o.source_id for o in used}
        kinds = {o.metric for o in used}
        independent = 1.0 if len(sources) >= 2 else (0.5 if len(kinds) >= 2 else 0.0)

        contra = 1.0 if any(c.state == "pass" for c in cand.contradicting_checks) else 0.0
        if not contra and cand.contradicting:
            contra = max(f.payload.get("score") if f.payload.get("score") is not None else 0.5
                         for f in _dedupe(cand.contradicting))

        raw = max(0.0, min(1.0, 0.40 * signal + 0.25 * topology + 0.20 * time_fit
                             + 0.15 * independent - 0.15 * contra))
        cand.factors = {"signal_fit": round(signal, 3), "topology_fit": topology, "time_fit": round(time_fit, 3),
                        "independent_source_fit": independent, "contradiction_strength": round(contra, 3),
                        "coverage": round(cand.coverage, 3), "severity": cand.severity}
        cand.confidence = round(raw * cand.coverage, 2)

    # ----------------------------------------------------------------- QUERY rounds

    def plan_queries(self, ctx: IncidentContext) -> list[Envelope]:
        """Round 1 verifies the leader; round 2 the runner-up when it is within 0.15. Max 2 rounds."""
        if self.rounds >= MAX_QUERY_ROUNDS:
            return []
        order = [c for c in self.ranking() if c.cause in KEY_MEASUREMENT and c.verified is None]
        if not order:
            return []
        ranked = self.ranking()
        if self.rounds == 0:
            pick = order[0]
        else:
            leader = ranked[0]
            close = [c for c in order if c is not leader and leader.confidence - c.confidence < CLOSE]
            if not close:
                return []
            pick = close[0]
        metric = KEY_MEASUREMENT[pick.cause]
        owner = next((a for a, card in CARDS.items() if metric in card.owned_metrics and card.endpoint == "local"), None)
        if owner is None:
            return []
        self.rounds += 1
        query_id = f"Q-{self.rounds:02d}"
        self._queried[query_id] = pick.hypothesis
        self.history.append(f"before {query_id}: " + _ranking_text(ranked))
        payload = QueryPayload(query_id=query_id, target_id=pick.target, desired_measurement=metric,
                               question=f"Verify {pick.hypothesis}: give every fresh {metric} sample on {pick.target} "
                                        "so its severity (critical or only warn level) can be judged",
                               related_hypotheses=[c.hypothesis for c in ranked[:2]])
        return [self.make(ctx, owner, MessageType.QUERY, payload.model_dump(mode="json"), None)]

    def absorb(self, response: Envelope, ctx: IncidentContext, findings: list[Envelope]) -> None:
        """Set the queried hypothesis's severity from the RESPONSE's evidence, then re-score all."""
        hypothesis = self._queried.get(response.payload.get("query_id") or "")
        cand = self.candidates.get(hypothesis or "")
        if cand is None:
            return
        evidence = [o for e in response.payload.get("evidence_ids") or [] if (o := ctx.evidence(e)) is not None]
        cand.severity, cand.verified = _severity(cand.cause, evidence, ctx)
        fresh = [o for o in evidence if o.source_id.startswith("on-demand")]
        cand.verified += (f"; from {len(fresh)} new on-demand probe sample(s)" if fresh else
                          "; no new probe was run: the window's own samples were re-read to grade severity")
        topo, ev = _Topo(ctx.topology), _Evidence(ctx)
        for c in self.candidates.values():
            self._score(c, ctx, topo, ev, findings)
        self.history.append(f"after {response.payload.get('query_id')}: " + _ranking_text(self.ranking())
                            + f" ({cand.hypothesis}: {cand.verified})")

    # ----------------------------------------------------------------- decision

    def decide(self, ctx: IncidentContext, in_reply_to: Optional[str]) -> tuple[Envelope, dict[str, Any]]:
        """The DECISION message plus what Reporting needs (status, leader, rivals, reasons)."""
        ranked = self.ranking()
        leader = ranked[0] if ranked else None
        rival = ranked[1] if len(ranked) > 1 else None
        lead = leader.confidence - (rival.confidence if rival else 0.0) if leader else 0.0
        limitations: list[str] = list(self.history)
        healthy, healthy_conf, healthy_notes = _healthy(ctx, ranked)
        # R1: a rival the agents raised but the evidence can neither confirm nor refute is unresolved.
        unresolved = [c for c in ranked if c is not leader and c.unresolved and c.supporting]
        for c in unresolved:
            senders = ", ".join(sorted({f.sender for f in c.supporting}))
            verb = "could not be assessed" if c.unassessed else "is inconclusive"
            limitations.append(f"{c.hypothesis} {verb} ({'; '.join(c.unknown_reasons())}); "
                               f"raised by {senders}, so it is not ruled out")
        alternatives = [c for c in ranked if c is not leader and not c.refuted]
        if leader and rival and rival.confidence == leader.confidence:
            limitations.append(f"{leader.hypothesis} and {rival.hypothesis} tie at confidence {leader.confidence:.2f}; "
                               f"ranked by signature evidence strength (signal_fit {leader.factors['signal_fit']} vs "
                               f"{rival.factors['signal_fit']})")

        corroborated = bool(leader) and leader.corroboration_met
        if corroborated and leader.required_pass and leader.confidence >= 0.70 and leader.coverage >= 0.75 \
                and leader.factors["independent_source_fit"] == 1.0 and lead >= CLOSE and not unresolved:
            status = Status.diagnosed
        elif healthy:
            status = Status.healthy
        elif corroborated and leader.confidence >= 0.50:
            status = Status.probable
            limitations += _not_diagnosed(leader, rival, lead)
        else:
            status = Status.insufficient_evidence
            limitations += healthy_notes
            if leader:
                limitations += _not_diagnosed(leader, rival, lead)
            else:
                limitations.append("no fault hypothesis was nominated with evidence; heuristic-v1 confidence 0.0")
        ranking = [RankedCause(hypothesis=c.hypothesis, confidence=c.confidence, coverage=round(c.coverage, 2),
                               supporting_finding_ids=sorted({f.payload["finding_id"] for f in c.supporting}),
                               contradicting_finding_ids=sorted({f.payload["finding_id"] for f in c.contradicting}))
                   for c in ranked[:5]]
        payload = DecisionPayload(decision_id=f"{ctx.incident_id}-DEC-01", status=status, ranking=ranking,
                                  limitations=limitations)
        msg = self.make(ctx, "reporting_agent", MessageType.DECISION, payload.model_dump(mode="json"), in_reply_to)
        return msg, {"status": status, "leader": leader, "rival": rival, "ranked": ranked,
                     "healthy_confidence": healthy_conf, "limitations": limitations,
                     "alternatives": alternatives, "unresolved": unresolved}


# --------------------------------------------------------------------------- check semantics


def _judge(check: str, metric: str, series: dict[str, list[Observation]], th: dict[str, Any],
           ctx: IncidentContext) -> tuple[str, list[Observation]]:
    """Apply one vocabulary check to the series of one location group."""
    warn, critical = th.get("warn"), th.get("critical")
    allobs = [o for s in series.values() for o in s]
    if check in ("failed", "reachable", "unreachable"):
        latest = [s[-1] for s in series.values() if _bool(s[-1]) is not None]
        if not latest:
            return "unknown", []
        values = [_bool(o) for o in latest]
        if check == "failed":
            bad = [o for o in latest if _bool(o) is False]
            return ("pass", bad) if bad else ("fail", latest)
        want = check == "reachable"
        return ("pass" if all(v is want for v in values) else "fail"), latest
    if check == "down_or_flapping":
        if metric == "interface_oper_status":
            latest = [s[-1] for s in series.values()]
            down = [o for o in latest if str(o.value).lower() in {"down", "flapping"}]
            return ("pass", down) if down else ("fail", latest)
        check = "rising_delta"
    if check == "weak_or_grey_zone":
        upper = ((ctx.signatures.get("rssi") or {}).get("grey_zone_dbm") or {}).get("upper_exclusive", -67)
        values = [v for o in allobs if (v := _num(o)) is not None]
        if not values:
            return "unknown", []
        return ("pass" if median(values) <= upper else "fail"), allobs
    if check == "rising_delta":
        if warn is None:
            return "unknown", []
        best: tuple[float, list[Observation]] = (0.0, [])
        for s in series.values():
            for a, b in zip(s, s[1:]):
                va, vb = _num(a), _num(b)
                if va is None or vb is None or "counter_reset" in b.quality_flags or vb < va:
                    continue
                if vb - va > best[0]:
                    best = (vb - va, [a, b])
        if not any(len(s) >= 2 for s in series.values()):
            return "unknown", []
        return ("pass", best[1]) if best[0] >= warn and best[0] > 0 else ("fail", allobs)
    limit = critical if check in ("sustained_above_critical", "above_critical") else warn
    if limit is None:
        return "unknown", []
    if check == "sustained_above_critical":
        need = (ctx.signatures.get("window") or {}).get("congestion_min_consecutive_samples", 3)
        for s in series.values():
            run = sustained_run(s, limit, warn, need)
            if run:
                return "pass", run
        if not any(len(s) >= need for s in series.values()):
            return "unknown", []
        # No sustained run. Refute only if the link is not even elevated; utilization that keeps
        # returning above warn without a clean critical run is inconclusive, not evidence against.
        elevated = warn is not None and any(sum(1 for o in s if (v := _num(o)) is not None and v > warn) >= need
                                            for s in series.values())
        return ("inconclusive", allobs) if elevated else ("fail", allobs)
    latest = [s[-1] for s in series.values() if _num(s[-1]) is not None]
    if not latest:
        return "unknown", []
    if check in ("above_warn", "above_critical"):
        hits = [o for o in latest if _num(o) > limit]
        return ("pass", hits) if hits else ("fail", latest)
    if check == "below_warn":
        return ("pass" if all(_num(o) <= limit for o in latest) else "fail"), latest
    return "unknown", []


def sustained_run(samples: list[Observation], critical: float, warn: Optional[float], need: int) -> list[Observation]:
    """The first run with >= `need` samples above critical. A sample in the warn band (above warn,
    at or below critical) neither counts nor ends the run; a sample at or below warn, or a
    missing one, ends it. With no warn threshold every non-critical sample ends the run."""
    run: list[Observation] = []
    step = _sample_interval(samples)
    previous = None
    for o in samples:
        if previous is not None and step and (o.observed_at - previous).total_seconds() > 1.5 * step:
            run = []                       # a missing interval ends the run: no skipping gaps
        previous = o.observed_at
        v = _num(o)
        if v is not None and v > critical:
            run.append(o)
            if len(run) >= need:
                return run
        elif v is None or warn is None or v <= warn:
            run = []
    return []


def _sample_interval(samples: list[Observation]) -> float:
    """Smallest positive spacing between samples, in seconds (0 if unknown)."""
    times = sorted({o.observed_at for o in samples})
    gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:]) if b > a]
    return min(gaps) if gaps else 0.0


def _severity(cause: str, evidence: list[Observation], ctx: IncidentContext) -> tuple[float, str]:
    """How severe the verified key measurement is: (severity, human-readable verdict)."""
    if not evidence:
        return 0.5, "QUERY returned no fresh evidence; severity unverified"
    metric = KEY_MEASUREMENT[cause]
    th = ctx.threshold(metric)
    warn, critical = th.get("warn"), th.get("critical")
    values = [v for o in sorted(evidence, key=lambda o: o.observed_at) if (v := _num(o)) is not None]
    if cause == "link_congestion" and critical is not None and values:
        above = sum(v > critical for v in values)
        if above == len(values):
            return 1.0, f"all {len(values)} utilization samples above {critical:g}%"
        return (0.6, f"{above}/{len(values)} samples above {critical:g}%") if above else \
            (0.2, f"no sample above {critical:g}%")
    if cause == "interface_fault" and warn is not None and len(values) >= 2:
        # Counter thresholds are per 1-minute sample interval; scale a faster probe to per-minute.
        timed = [(o.observed_at, v) for o in sorted(evidence, key=lambda o: o.observed_at) if (v := _num(o)) is not None]
        peak = max(((vb - va) * 60.0 / max((tb - ta).total_seconds(), 1.0)
                    for (ta, va), (tb, vb) in zip(timed, timed[1:]) if vb >= va), default=0.0)
        if critical is not None and peak >= critical:
            return 1.0, f"peak error rate {peak:g}/min reaches critical {critical:g}"
        return (0.4, f"peak error rate {peak:g}/min reaches only warn {warn:g}") if peak >= warn else \
            (0.1, f"peak error rate {peak:g}/min below warn")
    if cause == "weak_wifi" and values:
        zone = (ctx.signatures.get("rssi") or {}).get("grey_zone_dbm") or {}
        signal = median(values)
        return (1.0, f"median RSSI {signal:g} dBm is weak") if signal <= zone.get("lower_exclusive", -75) else \
            (0.5, f"median RSSI {signal:g} dBm is in the grey zone")
    if cause == "device_overload" and critical is not None and values:
        above = sum(v > critical for v in values)
        return (1.0, f"{above}/{len(values)} CPU samples above {critical:g}%") if above >= 3 else \
            (0.4, f"{above}/{len(values)} CPU samples above {critical:g}%")
    if cause == "hardware_failure":
        down = [o for o in evidence if str(o.value).lower() == "down"]
        return (1.0, "interface reported down") if down else (0.3, "interface not reported down")
    return 0.5, "severity not judged"


def _healthy(ctx: IncidentContext, ranked: list[Candidate]) -> tuple[bool, float, list[str]]:
    """The healthy gate (ARCHITECTURE §4): explicit normal measurements and passing checks."""
    ev = _Evidence(ctx)
    notes: list[str] = []
    perf = [o for o in ev.fresh if o.metric in PERFORMANCE_METRICS]
    infra = [o for o in ev.fresh if o.metric in INFRA_METRICS]
    services = [o for o in ev.fresh if o.metric in ("dns_success", "dhcp_success")]
    paths = [o for o in ev.fresh if o.metric == "reachability"]
    perf_normal = bool(perf) and all(
        (v := _num(o)) is None or ctx.threshold(o.metric).get("warn") is None
        or o.metric == "throughput_mbps" or v <= ctx.threshold(o.metric)["warn"] for o in perf)
    conditions = {
        "fresh performance measurements within normal range": perf_normal,
        "fresh infrastructure measurements": bool(infra),
        "service checks (DNS/DHCP) present and passing": bool(services) and all(_bool(o) is True for o in services),
        "path checks (reachability) present and passing": bool(paths) and all(_bool(o) is True for o in paths),
        "no missing or stale data": not ev.stale,
        "no candidate passes all its required checks": not any(c.required_pass for c in ranked),
    }
    for name, ok in conditions.items():
        if not ok:
            notes.append(f"healthy not shown: {name} — not met")
    met = sum(conditions.values()) / len(conditions)
    sources = {o.source_id for o in perf + infra + services + paths}
    raw = 0.40 * (1.0 if perf_normal else 0.0) + 0.25 * (1.0 if paths and conditions[
        "path checks (reachability) present and passing"] else 0.0) + 0.20 * (0.0 if ev.stale else 1.0) \
        + 0.15 * (1.0 if len(sources) >= 2 else 0.0)
    return all(conditions.values()), round(raw * met, 2), notes


def _not_diagnosed(leader: Candidate, rival: Optional[Candidate], lead: float) -> list[str]:
    out = []
    if not leader.corroboration_met:
        out.append(f"{leader.hypothesis}: corroboration not met (needs {leader.corroboration_need})")
    unmet = [c.name for c in leader.required if c.state != "pass"]
    if unmet:
        out.append(f"{leader.hypothesis}: required check(s) not met: {', '.join(unmet)}")
    if leader.coverage < 0.75:
        out.append(f"{leader.hypothesis}: coverage {leader.coverage:.2f} < 0.75")
    if leader.factors.get("independent_source_fit", 0) < 1.0:
        out.append(f"{leader.hypothesis}: evidence from fewer than two independent raw sources")
    if rival and lead < CLOSE:
        out.append(f"unresolved rival {rival.hypothesis} within {lead:.2f} of {leader.hypothesis}")
    if leader.confidence < 0.70:
        out.append(f"{leader.hypothesis}: confidence {leader.confidence:.2f} < 0.70")
    if not out:
        out.append(f"{leader.hypothesis}: not diagnosed while an unassessed rival remains open")
    return out


def _describe(item: dict[str, Any], ctx: IncidentContext) -> str:
    """Plain words for one corroborating check, with the YAML threshold it is judged against."""
    if "any_of" in item:
        return " or ".join(_describe(i, ctx) for i in item["any_of"])
    metric, check = item["metric"], item["check"]
    warn = ctx.threshold(metric).get("warn")
    unit = " ms" if metric.endswith("_ms") else " %" if metric.endswith("_pct") else ""
    name = metric[:-3] if unit == " ms" else metric
    if check == "rising_delta" and warn is not None:
        return f"{metric} rising \u2265 {warn:g}/interval"
    if check == "above_warn" and warn is not None:
        return f"{name} above {warn:g}{unit}"
    return f"{metric} {check}"


def _dedupe(findings: list[Envelope]) -> list[Envelope]:
    """One contradicting finding per evidence set: a measurement counts once."""
    seen: set[tuple[str, ...]] = set()
    out = []
    for f in findings:
        key = tuple(sorted(f.payload["evidence_ids"]))
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def _ranking_text(ranked: list[Candidate]) -> str:
    return ", ".join(f"{c.hypothesis} {c.confidence:.2f}" for c in ranked[:3]) or "no candidates"
