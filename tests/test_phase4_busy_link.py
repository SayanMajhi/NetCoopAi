"""Phase 3 re-review P4-0: a busy but unimpaired link is not congestion.

AI 3's reproduction: scenarios/dev/normal.json with only HOSTEL_UPLINK_01 changed. Latency stays normal
and queue drops keep the fixture's background growth (+5 per interval, below queue_drops.warn = 10).
High utilization alone must not reach `diagnosed` or `probable`: ARCHITECTURE section 3 requires
queue-drop or latency corroboration. No threshold, weight or gate value is changed for this.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from netcoop.orchestrator.run import default_components, load_signatures, load_topology, run_incident

ROOT = Path(__file__).resolve().parents[1]
NORMAL = json.loads((ROOT / "scenarios" / "dev" / "normal.json").read_text(encoding="utf-8"))
TOPOLOGY, SIGNATURES = load_topology(), load_signatures()
SPIKY = [91.5, 84.0, 92.0, 83.0, 93.0]
STEADY = [92.0, 94.0, 95.0, 93.0, 94.5]
LIMITATION = ("link_congestion@HOSTEL_UPLINK_01: corroboration not met "
              "(needs queue_drops rising \u2265 10/interval or latency above 100 ms)")


def busy(utilization: list[float], *, flat_drops: bool) -> dict:
    fx = copy.deepcopy(NORMAL)
    samples = iter(utilization)
    for r in fx["records"]:
        if r.get("link_id") == "HOSTEL_UPLINK_01" and "link_utilization_pct" in r["metrics"]:
            value = next(samples)
            r["metrics"]["link_utilization_pct"] = value
            r["metrics"]["throughput_mbps"] = value * 10
        if flat_drops and r.get("device_id") == "SW_CAMPUS_1" and "queue_drops" in r["metrics"]:
            r["metrics"]["queue_drops"] = 310
    assert next(samples, None) is None
    return fx


def run(fx: dict):
    return run_incident(fx, TOPOLOGY, SIGNATURES, components=default_components(connectivity="local"))


def test_fixture_really_has_background_drops_below_warn():
    drops = [r["metrics"]["queue_drops"] for r in NORMAL["records"]
             if r.get("device_id") == "SW_CAMPUS_1" and "queue_drops" in r["metrics"]]
    deltas = [b - a for a, b in zip(drops, drops[1:])]
    assert deltas and any(d > 0 for d in deltas) and all(0 <= d < 10 for d in deltas), deltas


@pytest.mark.parametrize("utilization,flat", [(SPIKY, False), (SPIKY, True), (STEADY, True)],
                         ids=["spiky-background-drops", "spiky-flat-drops", "steady-flat-drops"])
def test_busy_link_is_not_named_a_fault(utilization, flat):
    report = run(busy(utilization, flat_drops=flat)).report
    assert report.status == "insufficient_evidence", (report.status, report.suspected_root_cause)
    assert report.suspected_root_cause is None


def _over_nominating(ctx, scope, minutes):
    """The pre-P4-0a rule: any increase counts. Used to prove Correlation's gate (P4-0b) holds on its own."""
    from netcoop.agents.performance import _fresh
    samples = sorted((o for o in ctx.by_metric("queue_drops") if (o.device_id or o.link_id) in scope),
                     key=lambda o: (o.observed_at, o.evidence_id))
    for before, after in zip(samples, samples[1:]):
        if _fresh(ctx, before, minutes) and _fresh(ctx, after, minutes) and after.value > before.value:
            return [before, after]
    return []


def test_performance_no_longer_nominates_a_busy_link():
    result = run(busy(SPIKY, flat_drops=False))
    assert not [f for f in result.findings if f.sender == "performance_agent"
                and f.payload.get("stance") == "supports"], [f.payload for f in result.findings]


def test_correlation_gate_holds_even_if_an_agent_over_nominates(monkeypatch):
    monkeypatch.setattr("netcoop.agents.performance._rising_drops", _over_nominating)
    report = run(busy(SPIKY, flat_drops=False)).report
    assert report.status == "insufficient_evidence", (report.status, report.suspected_root_cause)
    assert report.suspected_root_cause is None
    assert LIMITATION in report.limitations, report.limitations
    assert any(a.startswith("Collect corroborating evidence for link_congestion@HOSTEL_UPLINK_01")
               for a in report.recommended_actions), report.recommended_actions


def test_real_congestion_is_still_diagnosed():
    report = run(json.loads((ROOT / "scenarios" / "dev" / "congestion.json").read_text(encoding="utf-8"))).report
    assert (report.status, report.suspected_root_cause) == ("diagnosed", "link_congestion@HOSTEL_UPLINK_01")
