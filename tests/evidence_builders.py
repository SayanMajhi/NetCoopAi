"""Shared builders for Infrastructure/Connectivity tests: canonical observations
and a signatures dict with test-only thresholds (the real YAML values come from B1;
these tests must not depend on them)."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from netcoop.agents.base import IncidentContext
from netcoop.orchestrator.run import load_signatures, load_topology
from netcoop.protocol.models import Observation

CLOCK = datetime(2026, 11, 1, 10, 5, tzinfo=timezone.utc)
TOPOLOGY = load_topology()
LINKS = {link["link_id"] for link in TOPOLOGY["links"]}

TEST_THRESHOLDS = {
    "latency_ms": (50, 150), "packet_loss_pct": (1, 5), "link_utilization_pct": (70, 90),
    "cpu_pct": (75, 90), "memory_pct": (80, 95), "queue_drops": (100, 1000),
    "interface_errors": (10, 100), "link_flap_count": (1, 3), "retry_rate_pct": (10, 25),
}


def signatures(thresholds: Optional[dict[str, tuple[Any, Any]]] = None) -> dict[str, Any]:
    sig = copy.deepcopy(load_signatures())
    for metric, (warn, critical) in (TEST_THRESHOLDS if thresholds is None else thresholds).items():
        sig["thresholds"][metric].update(warn=warn, critical=critical)
    return sig


def observations(spec: Iterable[tuple[str, str, list[Any]]], flags: Optional[dict[tuple[str, int], list[str]]] = None,
                 source: str = "switch-telemetry") -> list[Observation]:
    """spec rows are (target, metric, values); one sample per minute ending at CLOCK.
    flags maps (metric, sample index) -> quality flags for that sample."""
    out: list[Observation] = []
    for target, metric, values in spec:
        for i, value in enumerate(values):
            at = CLOCK - timedelta(minutes=len(values) - 1 - i, seconds=30)
            target_field = {"link_id": target} if target in LINKS else {"device_id": target}
            out.append(Observation(
                evidence_id=f"EV-{len(out) + 1:03d}", source_id=source, observed_at=at, metric=metric,
                value=value, unit="", original_field=f"metrics.{metric}", original_value=value,
                quality_flags=(flags or {}).get((metric, i), ["ok"]), **target_field))
    return out


def context(obs: list[Observation], sig: Optional[dict[str, Any]] = None, incident: str = "INC-0101") -> IncidentContext:
    return IncidentContext(incident_id=incident, clock=CLOCK, observations=obs, topology=TOPOLOGY,
                           signatures=signatures() if sig is None else sig)
