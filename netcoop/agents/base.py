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

    def __post_init__(self) -> None:
        if self.clock.tzinfo is None:
            raise ValueError("IncidentContext.clock must be timezone-aware")
        self._by_id = {o.evidence_id: o for o in self.observations}

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
