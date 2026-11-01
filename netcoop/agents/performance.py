"""Evidence-backed Performance findings about link congestion."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import timedelta

from netcoop.agents.base import BaseAgent, IncidentContext
from netcoop.protocol.models import Envelope, MessageType, Observation

_BAD = {"missing", "stale", "out_of_window", "malformed", "counter_reset"}


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _limit(ctx: IncidentContext, metric: str, level: str) -> float | None:
    return _number(ctx.threshold(metric).get(level))


def _fresh(ctx: IncidentContext, obs: Observation, minutes: float) -> bool:
    return (not _BAD.intersection(obs.quality_flags) and _number(obs.value) is not None
            and ctx.clock - timedelta(minutes=minutes) <= obs.observed_at <= ctx.clock)


def _scope(ctx: IncidentContext, target: str) -> set[str]:
    """A link and its downstream devices, or a device and its descendants."""
    links = [edge for edge in ctx.topology.get("links", []) if isinstance(edge, dict)]
    edge = next((e for e in links if e.get("link_id") == target), None)
    pending = [edge["b"]] if edge and isinstance(edge.get("b"), str) else [target]
    seen = {target} if edge else set()
    while pending:
        node = pending.pop()
        if node in seen:
            continue
        seen.add(node)
        pending.extend(e["b"] for e in links if e.get("a") == node and isinstance(e.get("b"), str))
    return seen


def _p95_above(ctx: IncidentContext, metric: str, scope: set[str], minutes: float) -> tuple[float, Observation] | None:
    warn = _limit(ctx, metric, "warn")
    if warn is None:
        return None
    samples = sorted((o for o in ctx.by_metric(metric)
                      if (o.device_id or o.link_id) in scope and _fresh(ctx, o, minutes)),
                     key=lambda o: (o.value, o.evidence_id))
    if not samples:
        return None
    obs = samples[math.ceil(0.95 * len(samples)) - 1]
    return (float(obs.value), obs) if obs.value > warn else None


def _rising_drops(ctx: IncidentContext, scope: set[str], minutes: float) -> list[Observation]:
    groups: dict[tuple[str, str], list[Observation]] = defaultdict(list)
    for obs in ctx.by_metric("queue_drops"):
        target = obs.device_id or obs.link_id
        if target in scope:
            groups[(target, obs.source_id)].append(obs)
    for key in sorted(groups):
        samples = sorted(groups[key], key=lambda o: (o.observed_at, o.evidence_id))
        for before, after in zip(samples, samples[1:]):
            if _fresh(ctx, before, minutes) and _fresh(ctx, after, minutes) and after.value > before.value:
                return [before, after]
    return []


def _streak(samples: list[Observation], ctx: IncidentContext, minutes: float,
            critical: float, minimum: int) -> list[Observation]:
    """`minimum` samples above critical in one run; a dip into the warn band does not end the run
    (signatures vocabulary `sustained_above_critical`), a sample at or below warn or a stale one does."""
    warn = _limit(ctx, "link_utilization_pct", "warn")
    times = sorted({o.observed_at for o in samples})
    step = min(((b - a).total_seconds() for a, b in zip(times, times[1:]) if b > a), default=0.0)
    consecutive: list[Observation] = []
    previous = None
    for obs in samples:
        if previous is not None and step and (obs.observed_at - previous).total_seconds() > 1.5 * step:
            consecutive.clear()            # a missing interval ends the run
        previous = obs.observed_at
        fresh = _fresh(ctx, obs, minutes)
        if fresh and obs.value > critical:
            consecutive.append(obs)
            if len(consecutive) >= minimum:
                return consecutive[-minimum:]
        elif not fresh or warn is None or obs.value <= warn:
            consecutive.clear()
    return []


def _throughput_context(ctx: IncidentContext, target: str, minutes: float) -> tuple[str, list[str]]:
    samples = [o for o in ctx.for_target(target, "throughput_mbps") if _fresh(ctx, o, minutes)]
    if not samples:
        return "", []
    obs = samples[-1]
    capacity_samples = [o for o in ctx.for_target(target, "capacity_mbps") if _fresh(ctx, o, minutes)]
    capacity = _number(capacity_samples[-1].value) if capacity_samples else None
    if capacity is None:
        capacity = next((_number(e.get("capacity_mbps")) for e in ctx.topology.get("links", [])
                         if isinstance(e, dict) and e.get("link_id") == target), None)
    if capacity is None or capacity <= 0:
        return "", []
    ratio = obs.value / capacity
    warn = _limit(ctx, "throughput_mbps", "warn")
    critical = _limit(ctx, "throughput_mbps", "critical")
    note = f"throughput {obs.value:g}/{capacity:g} Mbps"
    if critical is not None and ratio < critical:
        note += "; below critical capacity fraction, though low demand is possible"
    elif warn is not None and ratio < warn:
        note += "; below configured capacity fraction, though low demand is possible"
    return note, [obs.evidence_id]


class PerformanceAgent(BaseAgent):
    agent_id = "performance_agent"
    abbrev = "PERF"

    def handle(self, task: Envelope, ctx: IncidentContext) -> list[Envelope]:
        if task.type != MessageType.TASK.value or task.recipient != self.agent_id:
            return []
        window = ctx.signatures.get("window", {})
        minutes = _number(window.get("minutes"))
        minimum = window.get("congestion_min_consecutive_samples")
        warn = _limit(ctx, "link_utilization_pct", "warn")
        critical = _limit(ctx, "link_utilization_pct", "critical")
        if (minutes is None or minutes <= 0 or not isinstance(minimum, int) or isinstance(minimum, bool)
                or minimum < 1 or warn is None or critical is None):
            return []
        targets = sorted({o.device_id or o.link_id for o in ctx.by_metric("link_utilization_pct")})
        findings: list[Envelope] = []
        for target in targets:
            every = ctx.for_target(target, "link_utilization_pct")
            measured = [o for o in every if "derived" not in o.quality_flags]
            derived = [o for o in every if "derived" in o.quality_flags]
            samples = measured or derived
            if measured and derived and not _streak(measured, ctx, minutes, critical, minimum) \
                    and _streak(derived, ctx, minutes, critical, minimum):
                samples = derived      # the independent throughput/capacity estimate shows the run
            scope = _scope(ctx, target)
            high = _streak(samples, ctx, minutes, critical, minimum)
            latency = _p95_above(ctx, "latency_ms", scope, minutes)
            drops = _rising_drops(ctx, scope, minutes)
            hypothesis = f"link_congestion@{target}"
            if high and (latency or drops):
                ids = [o.evidence_id for o in high]
                reasons = [f"utilization above {critical:g}% for {minimum} consecutive samples"]
                if latency:
                    ids.append(latency[1].evidence_id)
                    reasons.append(f"latency p95 {latency[0]:g} ms above warning")
                if drops:
                    ids.extend(o.evidence_id for o in drops)
                    reasons.append("queue-drop counter rose")
                for metric, unit in (("jitter_ms", "ms"), ("packet_loss_pct", "%")):
                    signal = _p95_above(ctx, metric, scope, minutes)
                    if signal:
                        ids.append(signal[1].evidence_id)
                        reasons.append(f"{metric} p95 {signal[0]:g} {unit} above warning")
                note, throughput_ids = _throughput_context(ctx, target, minutes)
                if note:
                    ids.extend(throughput_ids)
                    reasons.append(note)
                findings.append(self.finding(ctx, "correlation_agent", task.message_id,
                                             hypothesis=hypothesis, stance="supports",
                                             evidence_ids=ids, reason="; ".join(reasons)))
            else:
                inside = [o for o in samples if ctx.clock - timedelta(minutes=minutes) <= o.observed_at <= ctx.clock]
                valid = [o for o in inside if _fresh(ctx, o, minutes)]
                if len(valid) >= minimum and len(valid) == len(inside) and all(o.value <= warn for o in valid):
                    findings.append(self.finding(ctx, "correlation_agent", task.message_id,
                                                 hypothesis=hypothesis, stance="contradicts",
                                                 evidence_ids=[o.evidence_id for o in valid],
                                                 reason=f"all {len(valid)} fresh utilization samples are at or below {warn:g}%"))
        return findings
