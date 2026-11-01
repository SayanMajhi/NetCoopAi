"""Final hardening: weak Wi-Fi without AP-uplink telemetry, a refuted cause is never named,
abstentions stay below the probable gate, and a hung in-process agent cannot stall an incident."""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path

from netcoop.orchestrator.run import default_components, load_signatures, load_topology, run_incident

ROOT = Path(__file__).resolve().parents[1]
TOPOLOGY, SIGNATURES = load_topology(), load_signatures()
WEAK = json.loads((ROOT / "scenarios" / "dev" / "weak_wifi.json").read_text(encoding="utf-8"))


def run(fx, components=None):
    return run_incident(fx, TOPOLOGY, SIGNATURES,
                        components=components or default_components(connectivity="local")).report


def test_weak_signal_and_retries_alone_are_not_called_healthy():
    # Only the wireless controller reports: RSSI and retries, no AP probe, no AP-uplink telemetry.
    fx = copy.deepcopy(WEAK)
    fx["records"] = [r for r in fx["records"] if r.get("link_id") != "HOSTEL_AP_LINK_01"
                     and not (r.get("device_id") == "HOSTEL_AP_01" and r["source_id"] == "client-probe")]
    fx.pop("on_query", None)
    report = run(fx)
    assert report.status == "probable", (report.status, report.limitations)
    assert report.suspected_root_cause == "weak_wifi@HOSTEL_AP_01"


def test_lossy_ap_uplink_rules_weak_wifi_out():
    fx = copy.deepcopy(WEAK)
    for r in fx["records"]:
        if r.get("link_id") == "HOSTEL_AP_LINK_01" and "packet_loss_pct" in r["metrics"]:
            r["metrics"]["packet_loss_pct"] = 6.0
    report = run(fx)
    assert report.status not in ("diagnosed", "probable") or not report.suspected_root_cause.startswith("weak_wifi")
    assert any("ruled out by a contradicting measurement" in l for l in report.limitations), report.limitations
    assert report.confidence <= 0.49


def test_dev_weak_wifi_still_diagnosed():
    report = run(WEAK)
    assert (report.status, report.suspected_root_cause) == ("diagnosed", "weak_wifi@HOSTEL_AP_01")


def test_hung_agent_times_out_and_the_diagnosis_completes():
    components = default_components(connectivity="local")
    agent = components.agents["infrastructure_agent"]
    original = agent.handle
    agent.handle = lambda task, ctx: (time.sleep(8), original(task, ctx))[1]
    started = time.time()
    report = run(json.loads((ROOT / "scenarios" / "dev" / "congestion.json").read_text(encoding="utf-8")), components)
    assert time.time() - started < 7
    assert any("AGENT_TIMEOUT" in l and "infrastructure_agent" in l for l in report.limitations), report.limitations
    assert report.status == "diagnosed" and report.suspected_root_cause == "link_congestion@HOSTEL_UPLINK_01"
