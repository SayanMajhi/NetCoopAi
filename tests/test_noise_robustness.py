"""Phase 3 review R3: the decision logic must not overclaim on slightly imperfect data.

AI 3's exact noise function (multiplicative Gaussian on gauges, ±2 dB-scale on RSSI,
counters untouched) on the 10 core scenarios, seeds 1-10, at sigma 0.05 and 0.10.
We additionally perturb the `on_query` probe samples, so a clean probe cannot hide noise.
Failures are to be fixed through decision logic, never by moving thresholds, weights or gates.
"""
from __future__ import annotations

import copy
import json
import random
from collections import Counter
from pathlib import Path

import pytest

from netcoop.orchestrator.run import default_components, load_signatures, load_topology, run_incident

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "scenarios" / "dev"
LABELS = json.loads((ROOT / "scenarios" / "labels.json").read_text(encoding="utf-8"))["scenarios"]
TOPOLOGY, SIGNATURES = load_topology(), load_signatures()
CORE = ["normal", "false_report", "counter_reset", "congestion", "interface_fault", "weak_wifi", "dns_failure",
        "dhcp_failure", "ambiguous_congestion_errors", "unit_mismatch"]
HEALTHY = {"normal", "false_report", "counter_reset"}
GAUGE = {"latency_ms", "jitter_ms", "packet_loss_pct", "throughput_mbps", "cpu_utilization_pct",
         "memory_pct", "retry_rate_pct", "rtt_sec", "throughput_kbps"}


def noisy(fx: dict, sigma: float, seed: int) -> dict:
    """AI 3's function (feedback3.md R3), applied to `records` and, in addition, to `on_query`."""
    rng = random.Random(seed)
    x = copy.deepcopy(fx)
    for r in x["records"] + x.get("on_query", []):
        for k, v in r["metrics"].items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                if k in GAUGE:
                    r["metrics"][k] = round(max(0, v * (1 + rng.gauss(0, sigma))), 3)
                elif k == "link_utilization_pct":
                    r["metrics"][k] = round(min(100, max(0, v * (1 + rng.gauss(0, sigma)))), 2)
                elif k == "rssi_dbm":
                    r["metrics"][k] = round(v + rng.gauss(0, sigma * 40), 1)
    return x


def outcomes(sigma: float) -> tuple[Counter, list, list, list]:
    tally: Counter = Counter()
    wrong, false_faults, wrong_diagnosed = [], [], []
    for name in CORE:
        fixture = json.loads((DEV / f"{name}.json").read_text(encoding="utf-8"))
        label = LABELS[f"{name}.json"]
        for seed in range(1, 11):
            report = run_incident(noisy(fixture, sigma, seed), TOPOLOGY, SIGNATURES,
                                  components=default_components(connectivity="local")).report
            cause = report.suspected_root_cause.split("@")[0] if report.suspected_root_cause else None
            named = report.status in ("diagnosed", "probable")
            if named and name in HEALTHY:
                false_faults.append((name, seed, report.suspected_root_cause))
                tally["false fault on healthy"] += 1
            elif named and cause != label["cause"]:
                wrong.append((name, seed, report.status, report.suspected_root_cause, report.alternative_causes))
                tally["wrong cause"] += 1
                if report.status == "diagnosed":
                    wrong_diagnosed.append(wrong[-1])
            elif report.status == label["status"] and cause == label["cause"]:
                tally["exact"] += 1
            elif report.status == "probable" and label["status"] == "diagnosed":
                tally["probable instead of diagnosed"] += 1
            else:
                tally[f"abstain ({report.status})"] += 1
    return tally, wrong, false_faults, wrong_diagnosed


@pytest.mark.parametrize("sigma", [0.05, 0.10])
def test_no_overclaim_under_noise(sigma: float) -> None:
    tally, wrong, false_faults, wrong_diagnosed = outcomes(sigma)
    print(f"\nsigma={sigma}: {dict(tally)}")
    for w in wrong:
        print("  wrong cause:", w)
    assert sum(tally.values()) == 100
    assert false_faults == [], false_faults                     # never a fault on a healthy network
    assert wrong_diagnosed == [], wrong_diagnosed               # a wrong cause is never *diagnosed*
    if sigma == 0.05:
        assert wrong == [], wrong                               # AI 3's bar at 5 %: no wrong cause at all
    for name, seed, status, hypothesis, alternatives in wrong:  # at 10 %: only a hedged answer is tolerated
        label_cause = LABELS[f"{name}.json"]["cause"]
        assert status == "probable" and any(a.startswith(f"{label_cause}@") for a in alternatives), (name, seed)
    assert len(wrong) <= 1, wrong
