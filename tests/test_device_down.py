"""A device that stops answering is localised: Connectivity nominates hardware_failure at the topmost
silent point of the outage (whole subtree silent, something beside it still answers), and Correlation
reports it as `probable` (reachability is one source), never `diagnosed` and never a blank answer."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from netcoop.orchestrator.run import default_components, load_signatures, load_topology, run_incident

ROOT = Path(__file__).resolve().parents[1]
TOPOLOGY, SIGNATURES = load_topology(), load_signatures()
NORMAL = json.loads((ROOT / "scenarios" / "dev" / "normal.json").read_text(encoding="utf-8"))


def down(*devices: str, answers_once: bool = False) -> dict:
    fx = copy.deepcopy(NORMAL)
    fx["complaint"] = "Users report the network is down"
    seen: dict[str, int] = {}
    for r in fx["records"]:
        if r.get("device_id") in devices and "reachability" in r["metrics"]:
            seen[r["device_id"]] = seen.get(r["device_id"], 0) + 1
            r["metrics"]["reachability"] = answers_once and seen[r["device_id"]] == 3
    assert set(seen) == set(devices), seen
    return fx


def run(fx: dict):
    return run_incident(fx, TOPOLOGY, SIGNATURES, components=default_components(connectivity="local"))


@pytest.mark.parametrize("devices,suspect", [
    (("SW_CAMPUS_6",), "SW_CAMPUS_6"),                                            # one access switch
    (("SRV_DNS_DHCP_01",), "SRV_DNS_DHCP_01"),                                    # the server
    (("SW_CAMPUS_3", "SW_CAMPUS_4", "SW_CAMPUS_5", "SW_CAMPUS_6"), "SW_CAMPUS_1"),  # whole hostel: its distribution
])
def test_silent_device_is_localised_as_probable_hardware_failure(devices, suspect):
    report = run(down(*devices)).report
    assert (report.status, report.suspected_root_cause) == ("probable", f"hardware_failure@{suspect}"), \
        (report.status, report.suspected_root_cause, report.limitations)
    assert report.affected_devices == [suspect]
    assert report.confidence <= 0.69
    assert any(a.startswith("Confirm before acting") and suspect in a for a in report.recommended_actions)
    assert any("fewer than two independent raw sources" in l for l in report.limitations)


def test_two_silent_switches_name_one_and_keep_the_other_open():
    report = run(down("SW_CAMPUS_5", "SW_CAMPUS_6")).report
    assert report.status == "probable"
    assert {report.suspected_root_cause, *report.alternative_causes} >= {"hardware_failure@SW_CAMPUS_5",
                                                                           "hardware_failure@SW_CAMPUS_6"}


def test_a_device_that_answered_once_is_not_called_down():
    report = run(down("SW_CAMPUS_6", answers_once=True)).report
    assert report.status not in ("diagnosed", "probable")


def test_connectivity_cites_only_its_own_reachability_checks():
    result = run(down("SW_CAMPUS_6"))
    finding = next(f for f in result.findings if f.payload["hypothesis"] == "hardware_failure@SW_CAMPUS_6")
    assert finding.sender == "connectivity_agent" and finding.payload["stance"] == "supports"
    cited = [o for o in result.report.supporting_evidence if o.evidence_id in finding.payload["evidence_ids"]]
    assert cited and all(e.metric == "reachability" and e.value is False for e in cited)


def test_normal_network_nominates_no_hardware_failure():
    result = run(copy.deepcopy(NORMAL))
    assert result.report.status == "healthy"
    assert not [f for f in result.findings if f.payload["hypothesis"].startswith("hardware_failure@")]
