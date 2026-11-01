"""Infrastructure Agent: device health, interface counters, flaps and Wi-Fi radio.

Counters (interface_errors, link_flap_count, queue_drops) are judged by their
per-interval deltas; a negative delta or a `counter_reset` flag is a reset and is
never counted as evidence. The agent emits `supports` findings for faults it sees
and `contradicts` findings when its evidence argues against a rival cause (for
example interface errors rising on a link make congestion a weaker explanation).

A threshold that is still null in the signatures config makes that check
unavailable; it is never treated as passing or failing.
"""
from __future__ import annotations

from statistics import median
from typing import Any, Optional

from netcoop.agents.base import BaseAgent, IncidentContext
from netcoop.protocol.models import Envelope, Observation, QualityFlag, Stance

RECIPIENT = "correlation_agent"
METRICS = ("cpu_pct", "memory_pct", "queue_drops", "interface_errors", "link_flap_count",
           "interface_oper_status", "rssi_dbm", "retry_rate_pct")
UNUSABLE = {QualityFlag.missing.value, QualityFlag.stale.value, QualityFlag.malformed.value,
            QualityFlag.out_of_window.value}
DOWN_VALUES = {"down", "false", "0", "0.0", "lowerlayerdown", "notpresent"}


# --------------------------------------------------------------------------- evidence helpers


def usable(observations: list[Observation]) -> list[Observation]:
    """Drop missing/stale/malformed samples: unknown is not evidence either way."""
    return [o for o in observations if o.value is not None and not UNUSABLE.intersection(o.quality_flags)]


def numeric(o: Observation) -> Optional[float]:
    if isinstance(o.value, bool):
        return None
    try:
        return float(o.value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def numeric_samples(observations: list[Observation]) -> list[tuple[Observation, float]]:
    return [(o, v) for o in usable(observations) if (v := numeric(o)) is not None]


def level(value: float, threshold: dict[str, Any]) -> str:
    """critical / warn / normal for a 'high is bad' metric; unknown if thresholds are null."""
    warn, critical = threshold.get("warn"), threshold.get("critical")
    if critical is not None and value > critical:
        return "critical"
    if warn is not None and value > warn:
        return "warn"
    return "normal" if warn is not None else "unknown"


def sustained_above(samples: list[tuple[Observation, float]], limit: Optional[float], run: int) -> list[str]:
    """Evidence IDs of the longest run above `limit`, if it is at least `run` samples long."""
    if limit is None:
        return []
    best: list[str] = []
    current: list[str] = []
    for o, v in samples:
        current = current + [o.evidence_id] if v > limit else []
        if len(current) > len(best):
            best = current
    return best if len(best) >= run else []


def counter_deltas(observations: list[Observation]) -> tuple[list[tuple[float, list[str]]], list[str]]:
    """([(positive per-interval delta, IDs behind it)], IDs that are resets).

    Values are treated as cumulative counters unless the normalizer already turned
    them into deltas (a normalization step mentioning 'delta')."""
    samples = numeric_samples(observations)
    deltas: list[tuple[float, list[str]]] = []
    resets: list[str] = []
    if samples and any("delta" in step for step in samples[0][0].normalization_steps):
        for o, v in samples:
            if QualityFlag.counter_reset.value in o.quality_flags or v < 0:
                resets.append(o.evidence_id)
            elif v > 0:
                deltas.append((v, [o.evidence_id]))
        return deltas, resets
    for (prev, pv), (cur, cv) in zip(samples, samples[1:]):
        if QualityFlag.counter_reset.value in cur.quality_flags or cv < pv:
            resets.append(cur.evidence_id)
        elif cv > pv:
            deltas.append((cv - pv, [prev.evidence_id, cur.evidence_id]))
    return deltas, resets


def counter_growth(observations: list[Observation]) -> tuple[float, list[str], list[str]]:
    """(sum of positive per-interval deltas, IDs that show growth, IDs that are resets)."""
    deltas, resets = counter_deltas(observations)
    rising = sorted({e for _, ids in deltas for e in ids})
    return sum(d for d, _ in deltas), rising, resets


def peak_delta(observations: list[Observation]) -> float:
    """Largest single-interval growth; the YAML `critical` for counters is per interval."""
    deltas, _ = counter_deltas(observations)
    return max((d for d, _ in deltas), default=0.0)


def is_rising(growth: float, threshold: dict[str, Any]) -> bool:
    """rising_delta: positive growth, at least `warn` when a warn threshold is set."""
    warn = threshold.get("warn")
    return growth > 0 and (warn is None or growth >= warn)


def is_down(o: Observation) -> bool:
    return str(o.value).strip().lower() in DOWN_VALUES


# --------------------------------------------------------------------------- agent


class InfrastructureAgent(BaseAgent):
    agent_id = "infrastructure_agent"
    abbrev = "INF"

    def handle(self, msg: Envelope, ctx: IncidentContext) -> list[Envelope]:
        out: list[Envelope] = []
        for target in self._targets(ctx):
            out += self._device_overload(msg, ctx, target)
            out += self._interface(msg, ctx, target)
            out += self._queue_drops(msg, ctx, target)
            out += self._wifi(msg, ctx, target)
        return out

    @staticmethod
    def _targets(ctx: IncidentContext) -> list[str]:
        return sorted({t for o in ctx.observations if o.metric in METRICS for t in (o.device_id, o.link_id) if t})

    def _emit(self, msg: Envelope, ctx: IncidentContext, **kw: Any) -> Envelope:
        return self.finding(ctx, RECIPIENT, msg.message_id, **kw)

    # ----------------------------------------------------------------- device_overload

    def _device_overload(self, msg: Envelope, ctx: IncidentContext, target: str) -> list[Envelope]:
        run = int((ctx.signatures.get("window") or {}).get("congestion_min_consecutive_samples", 3))
        cited: list[str] = []
        parts: list[str] = []
        for metric in ("cpu_pct", "memory_pct"):
            samples = numeric_samples(ctx.for_target(target, metric))
            ids = sustained_above(samples, ctx.threshold(metric).get("critical"), run)
            if ids:
                cited += ids
                parts.append(f"{metric} above {ctx.threshold(metric)['critical']} for {len(ids)} consecutive samples")
        if not cited:
            return []
        return [self._emit(msg, ctx, hypothesis=f"device_overload@{target}", stance=Stance.supports,
                           evidence_ids=cited, score=0.8, reason="; ".join(parts))]

    # ----------------------------------------------------------------- interface / hardware

    def _interface(self, msg: Envelope, ctx: IncidentContext, target: str) -> list[Envelope]:
        err_growth, err_ids, err_resets = counter_growth(ctx.for_target(target, "interface_errors"))
        flap_growth, flap_ids, _ = counter_growth(ctx.for_target(target, "link_flap_count"))
        errors_up = is_rising(err_growth, ctx.threshold("interface_errors"))
        flaps_up = is_rising(flap_growth, ctx.threshold("link_flap_count"))
        status = usable(ctx.for_target(target, "interface_oper_status"))
        down = status[-1] if status and is_down(status[-1]) else None

        out: list[Envelope] = []
        if down is not None:
            corroboration = (err_ids if errors_up else []) + (flap_ids if flaps_up else [])
            out.append(self._emit(
                msg, ctx, hypothesis=f"hardware_failure@{target}", stance=Stance.supports,
                evidence_ids=[down.evidence_id, *corroboration], score=0.8 if corroboration else 0.5,
                reason=f"interface_oper_status is '{down.value}'"
                       + (" with rising error/flap counters" if corroboration else "; no corroborating counters")))
        elif errors_up or flaps_up:
            cited = (err_ids if errors_up else []) + (flap_ids if flaps_up else [])
            critical = ctx.threshold("interface_errors").get("critical")
            severe = errors_up and critical is not None and \
                peak_delta(ctx.for_target(target, "interface_errors")) >= critical
            score = 0.8 if severe or flaps_up else 0.6
            parts = ([f"interface_errors rose by {err_growth:g} in the window"] if errors_up else []) + \
                    ([f"link_flap_count rose by {flap_growth:g}"] if flaps_up else [])
            out.append(self._emit(msg, ctx, hypothesis=f"interface_fault@{target}", stance=Stance.supports,
                                  evidence_ids=cited, score=score, reason="; ".join(parts)))
            out += self._contradict_congestion(msg, ctx, target, cited)
        elif err_resets:
            out.append(self._emit(
                msg, ctx, hypothesis=f"healthy@{target}", stance=Stance.contradicts,
                target_hypothesis=f"interface_fault@{target}", evidence_ids=err_resets, score=0.6,
                reason="interface_errors counter dropped (reset, e.g. reboot); a reset is not error growth"))
        return out

    def _contradict_congestion(self, msg: Envelope, ctx: IncidentContext, target: str,
                               fault_ids: list[str]) -> list[Envelope]:
        """Rising errors argue against congestion on the links at this target."""
        out: list[Envelope] = []
        threshold = ctx.threshold("link_utilization_pct")
        for link in related_links(ctx.topology, target):
            samples = numeric_samples(ctx.for_target(link, "link_utilization_pct"))
            if not samples:
                continue
            util = median(v for _, v in samples)
            state = level(util, threshold)
            if state == "unknown":
                continue
            if state == "normal":
                score, why = 0.8, f"interface errors rising while {link} utilization is normal (median {util:g}%)"
            else:
                score, why = 0.4, (f"interface errors rising alongside high {link} utilization (median {util:g}%); "
                                   "congestion alone does not produce interface errors")
            out.append(self._emit(msg, ctx, hypothesis=f"interface_fault@{target}", stance=Stance.contradicts,
                                  target_hypothesis=f"link_congestion@{link}",
                                  evidence_ids=[*fault_ids, *(o.evidence_id for o, _ in samples)],
                                  score=score, reason=why))
        return out

    # ----------------------------------------------------------------- queue drops

    def _queue_drops(self, msg: Envelope, ctx: IncidentContext, target: str) -> list[Envelope]:
        """Rising drops plus high utilization on an adjacent link corroborate congestion there."""
        growth, ids, _ = counter_growth(ctx.for_target(target, "queue_drops"))
        if not is_rising(growth, ctx.threshold("queue_drops")):
            return []
        threshold = ctx.threshold("link_utilization_pct")
        best: Optional[tuple[float, str, list[str]]] = None
        for link in related_links(ctx.topology, target):
            samples = numeric_samples(ctx.for_target(link, "link_utilization_pct"))
            if not samples:
                continue
            util = median(v for _, v in samples)
            if level(util, threshold) in ("warn", "critical") and (best is None or util > best[0]):
                best = (util, link, [o.evidence_id for o, _ in samples])
        if best is None:
            return []
        util, link, util_ids = best
        return [self._emit(msg, ctx, hypothesis=f"link_congestion@{link}", stance=Stance.supports,
                           evidence_ids=[*ids, *util_ids], score=0.7,
                           reason=f"queue_drops on {target} rose by {growth:g} while {link} "
                                  f"utilization is high (median {util:g}%)")]

    # ----------------------------------------------------------------- wi-fi

    def _wifi(self, msg: Envelope, ctx: IncidentContext, target: str) -> list[Envelope]:
        rssi = numeric_samples(ctx.for_target(target, "rssi_dbm"))
        if not rssi:
            return []
        zone = (ctx.signatures.get("rssi") or {}).get("grey_zone_dbm") or {}
        lower, upper = zone.get("lower_exclusive", -75), zone.get("upper_exclusive", -67)
        signal = median(v for _, v in rssi)
        band = "weak" if signal <= lower else ("grey" if signal <= upper else "strong")
        rssi_ids = [o.evidence_id for o, _ in rssi]

        retries = numeric_samples(ctx.for_target(target, "retry_rate_pct"))
        retry_ids = [o.evidence_id for o, _ in retries]
        retry_state = level(median(v for _, v in retries), ctx.threshold("retry_rate_pct")) if retries else "unknown"
        retries_high = retry_state in ("warn", "critical")

        if band != "strong" and retries_high:
            score = 0.8 if band == "weak" else 0.5
            note = "" if band == "weak" else " (grey zone, uncertain on its own)"
            return [self._emit(msg, ctx, hypothesis=f"weak_wifi@{target}", stance=Stance.supports,
                               evidence_ids=rssi_ids + retry_ids, score=score,
                               reason=f"median RSSI {signal:g} dBm{note} with high retry rate")]
        if band == "weak":
            return [self._emit(msg, ctx, hypothesis=f"weak_wifi@{target}", stance=Stance.supports,
                               evidence_ids=rssi_ids + retry_ids, score=0.4,
                               reason=f"median RSSI {signal:g} dBm is weak but retries do not corroborate")]
        if band == "strong" and retry_state in ("normal", "unknown"):
            return [self._emit(msg, ctx, hypothesis=f"healthy@{target}", stance=Stance.contradicts,
                               target_hypothesis=f"weak_wifi@{target}", evidence_ids=rssi_ids + retry_ids,
                               score=0.7 if retry_state == "normal" else 0.5,
                               reason=f"median RSSI {signal:g} dBm is strong"
                                      + (" and retries are normal" if retry_state == "normal" else ""))]
        return []  # grey zone without retry evidence: uncertain, say nothing


def related_links(topology: dict[str, Any], target: str) -> list[str]:
    """The target itself if it is a link, else the links attached to that device."""
    links = topology.get("links") or []
    if any(link.get("link_id") == target for link in links):
        return [target]
    return sorted(link["link_id"] for link in links if target in (link.get("a"), link.get("b")))
