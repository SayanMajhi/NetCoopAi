"""Shared agent plumbing. Agents import only netcoop.protocol, this module and
config — never each other."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Optional

from netcoop.protocol.models import (
    PROTOCOL_VERSION,
    Envelope,
    FindingPayload,
    ResponsePayload,
    MessageType,
    Observation,
    Stance,
)


@dataclass
class IncidentContext:
    """Read-only view of one incident, handed to every agent."""

    incident_id: str
    clock: datetime                       # deterministic incident time (window end), timezone-aware
    observations: list[Observation]
    topology: dict[str, Any]
    signatures: dict[str, Any]            # parsed netcoop/config/signatures-1.0.yaml
    normalization_log: list[str] = field(default_factory=list)
    on_demand: list[Observation] = field(default_factory=list)   # probe samples, released by probe()
    complaint: str = ""                   # the operator's free-text report; scopes the healthy gate
    input_warnings: list[str] = field(default_factory=list)   # telemetry skipped or doubtful; blocks healthy

    def __post_init__(self) -> None:
        if self.clock.tzinfo is None:
            raise ValueError("IncidentContext.clock must be timezone-aware")
        self._by_id = {o.evidence_id: o for o in self.observations}

    def probe(self, targets: list[str], metric: str) -> list[Observation]:
        """Run the on-demand probe for `metric` on the first target that has one: its samples
        become evidence of this incident (new IDs) and are returned. Each probe runs once."""
        for target in targets:
            found = [o for o in self.on_demand if target in (o.device_id, o.link_id) and o.metric == metric]
            if found:
                self.on_demand = [o for o in self.on_demand if o not in found]
                self.observations.extend(found)
                self._by_id.update({o.evidence_id: o for o in found})
                return sorted(found, key=lambda o: (o.observed_at, o.evidence_id))
        return []

    def evidence(self, evidence_id: str) -> Optional[Observation]:
        return self._by_id.get(evidence_id)

    def by_metric(self, metric: str) -> list[Observation]:
        """Observations of one metric, oldest first."""
        return sorted((o for o in self.observations if o.metric == metric), key=_order)

    def for_target(self, target_id: str, metric: Optional[str] = None) -> list[Observation]:
        """Observations of a device or link (optionally one metric), oldest first."""
        return sorted(
            (o for o in self.observations
             if target_id in (o.device_id, o.link_id) and (metric is None or o.metric == metric)),
            key=_order,
        )

    def threshold(self, metric: str) -> dict[str, Any]:
        """Threshold entry for a metric from the signatures config (empty if none)."""
        return dict(self.signatures.get("thresholds", {}).get(metric) or {})


def _order(o: Observation) -> tuple[Any, ...]:
    return (o.observed_at, o.evidence_id)


class BaseAgent:
    """Subclasses set `agent_id` and `abbrev` and implement `handle`."""

    agent_id: str = "base"
    abbrev: str = "BASE"   # used in deterministic IDs: INC-0001-PERF-01, F-PERF-01

    def __init__(self) -> None:
        self._seq = 0
        self._finding_seq = 0

    def handle(self, msg: Envelope, ctx: IncidentContext) -> list[Envelope]:
        raise NotImplementedError

    def make(self, ctx: IncidentContext, recipient: str, type_: MessageType,
             payload: dict[str, Any], in_reply_to: Optional[str]) -> Envelope:
        """The only way agents create messages: deterministic ID, incident clock, validated."""
        self._seq += 1
        return Envelope(
            protocol=PROTOCOL_VERSION,
            message_id=f"{ctx.incident_id}-{self.abbrev}-{self._seq:02d}",
            incident_id=ctx.incident_id,
            in_reply_to=in_reply_to,
            sender=self.agent_id,
            recipient=recipient,
            type=type_,
            timestamp=ctx.clock,
            payload=payload,
        )

    def finding(self, ctx: IncidentContext, recipient: str, in_reply_to: Optional[str], *,
                hypothesis: str, stance: Stance | str, evidence_ids: Iterable[str], reason: str,
                target_hypothesis: Optional[str] = None, score: Optional[float] = None,
                consumed_message_ids: Iterable[str] = ()) -> Envelope:
        """Build a FINDING. Refuses to cite evidence that is not in this incident."""
        cited = sorted(set(evidence_ids))
        unknown = [e for e in cited if ctx.evidence(e) is None]
        if unknown:
            raise ValueError(f"{self.agent_id} cites unknown evidence {unknown}")
        self._finding_seq += 1
        payload = FindingPayload(
            finding_id=f"F-{self.abbrev}-{self._finding_seq:02d}",
            hypothesis=hypothesis,
            stance=stance,
            target_hypothesis=target_hypothesis or hypothesis,
            evidence_ids=cited,
            reason=reason,
            score=score,
            consumed_message_ids=list(consumed_message_ids),
        )
        return self.make(ctx, recipient, MessageType.FINDING, payload.model_dump(mode="json"), in_reply_to)

    def answer(self, query: Envelope, ctx: IncidentContext) -> Envelope:
        """Answer a Correlation QUERY: every fresh, usable sample of the desired measurement on
        the target (or, for a link, its endpoints), so Correlation can judge severity itself."""
        p = query.payload
        metric, target = p["desired_measurement"], p["target_id"]
        links = {l.get("link_id"): l for l in ctx.topology.get("links") or [] if isinstance(l, dict)}
        places = [target] + ([links[target]["b"], links[target]["a"]] if target in links else [])
        probed = ctx.probe(places, metric)
        if probed:
            answer = (f"ran an on-demand {metric} probe on {probed[0].device_id or probed[0].link_id}: "
                      f"{len(probed)} new sample(s) {[o.value for o in probed]}")
            payload = ResponsePayload(query_id=p["query_id"], evidence_ids=[o.evidence_id for o in probed], answer=answer)
            return self.make(ctx, query.sender, MessageType.RESPONSE, payload.model_dump(mode="json"), query.message_id)
        usable = []
        for place in places:
            usable = [o for o in ctx.for_target(place, metric) if o.value is not None
                      and not {"missing", "stale", "out_of_window", "malformed"} & set(o.quality_flags)]
            if usable:
                break
        values = [o.value for o in usable]
        answer = (f"no on-demand probe available: re-read {len(usable)} {metric} sample(s) already in the window on "
                  f"{usable[0].device_id or usable[0].link_id}: {values}; no new probe was run"
                  if usable else f"no fresh {metric} sample on {target} and no on-demand probe available")
        payload = ResponsePayload(query_id=p["query_id"], evidence_ids=[o.evidence_id for o in usable], answer=answer)
        return self.make(ctx, query.sender, MessageType.RESPONSE, payload.model_dump(mode="json"), query.message_id)
