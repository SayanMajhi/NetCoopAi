"""Infrastructure Agent (C1): counters by delta, resets ignored, contradicts rivals."""
from __future__ import annotations

from typing import Any

from netcoop.agents.base import BaseAgent
from netcoop.agents.infrastructure import InfrastructureAgent, counter_growth, peak_delta
from netcoop.protocol.models import Envelope, MessageType
from tests.evidence_builders import context, observations, signatures

TASK_ID = "INC-0101-ORCH-02"


def task(ctx) -> Envelope:
    orch = BaseAgent()
    orch.agent_id, orch.abbrev = "orchestrator", "ORCH"
    orch._seq = 1
    return orch.make(ctx, "infrastructure_agent", MessageType.TASK,
                     {"task_id": "INC-0101-T-INFRASTRUCTURE", "capability": "analyze_infrastructure"}, None)


def run(spec, flags=None, sig=None) -> list[dict[str, Any]]:
    ctx = context(observations(spec, flags), sig)
    out = InfrastructureAgent().handle(task(ctx), ctx)
    for m in out:
        Envelope.model_validate(m.model_dump(mode="json"))
        assert m.in_reply_to == TASK_ID and m.sender == "infrastructure_agent"
        assert m.payload["evidence_ids"] and all(ctx.evidence(e) for e in m.payload["evidence_ids"])
    return [m.payload for m in out]


def find(payloads, hypothesis: str, stance: str = "supports") -> list[dict[str, Any]]:
    return [p for p in payloads if p["hypothesis"] == hypothesis and p["stance"] == stance]


ERRORS_RISING = ("DEPT_UPLINK_01", "interface_errors", [100, 140, 190, 260, 340])
# The interface_fault signature needs packet loss downstream of the target too.
DEPT_LOSS = ("SW_CAMPUS_8", "packet_loss_pct", [3, 4, 5, 4, 6])
ACC7_LOSS = ("SW_CAMPUS_7", "packet_loss_pct", [3, 4, 5, 4, 6])


def test_interface_errors_with_normal_utilization_contradict_congestion() -> None:
    out = run([ERRORS_RISING, ("DEPT_UPLINK_01", "link_utilization_pct", [35, 38, 36, 40, 37]), DEPT_LOSS])
    assert find(out, "interface_fault@DEPT_UPLINK_01")
    contra = find(out, "interface_fault@DEPT_UPLINK_01", "contradicts")
    assert contra and contra[0]["target_hypothesis"] == "link_congestion@DEPT_UPLINK_01"
    assert contra[0]["score"] == 0.8 and "normal" in contra[0]["reason"]


def test_ambiguous_congestion_errors_emits_contradicts() -> None:
    out = run([ERRORS_RISING, ("DEPT_UPLINK_01", "link_utilization_pct", [93, 95, 96, 94, 97]), DEPT_LOSS])
    contra = find(out, "interface_fault@DEPT_UPLINK_01", "contradicts")
    assert contra and contra[0]["target_hypothesis"] == "link_congestion@DEPT_UPLINK_01"
    assert contra[0]["score"] == 0.4


def test_errors_on_a_device_check_its_attached_links() -> None:
    out = run([("SW_CAMPUS_7", "interface_errors", [0, 30, 70, 120, 180]),
               ("ACC_LINK_07", "link_utilization_pct", [20, 22, 21, 25, 23]), ACC7_LOSS])
    assert find(out, "interface_fault@SW_CAMPUS_7")
    assert [p["target_hypothesis"] for p in find(out, "interface_fault@SW_CAMPUS_7", "contradicts")] == \
        ["link_congestion@ACC_LINK_07"]


def test_counter_reset_is_not_error_growth() -> None:
    reset = ("SW_CAMPUS_1", "interface_errors", [500, 502, 3, 4, 5])
    growth, rising, resets = counter_growth(observations([reset]))
    assert growth == 4 and resets == ["EV-003"]
    out = run([reset])
    assert not find(out, "interface_fault@SW_CAMPUS_1")
    healthy = find(out, "healthy@SW_CAMPUS_1", "contradicts")
    assert healthy and healthy[0]["evidence_ids"] == ["EV-003"]


def test_counter_reset_flag_from_normalizer_is_respected() -> None:
    spec = [("SW_CAMPUS_1", "interface_errors", [10, 12, 400, 402, 404])]
    out = run(spec, flags={("interface_errors", 2): ["counter_reset"]})
    assert not find(out, "interface_fault@SW_CAMPUS_1")


def test_missing_values_are_ignored_not_zero() -> None:
    spec = [("SW_CAMPUS_1", "interface_errors", [100, None, 101, None, 102])]
    flags = {("interface_errors", 1): ["missing"], ("interface_errors", 3): ["missing"]}
    growth, rising, resets = counter_growth(observations(spec, flags))
    assert (growth, resets) == (2, [])            # a null read as 0 would have looked like a reset
    assert run(spec, flags) == []                 # +2 is below the warn delta


def test_link_flaps_support_interface_fault() -> None:
    out = run([("ACC_LINK_07", "link_flap_count", [0, 1, 2, 4, 5]), ACC7_LOSS])
    assert find(out, "interface_fault@ACC_LINK_07")[0]["score"] == 0.8


def test_oper_status_down_is_hardware_failure() -> None:
    out = run([("SW_CAMPUS_4", "interface_oper_status", ["up", "up", "up", "down", "down"]),
               ("SW_CAMPUS_4", "interface_errors", [0, 20, 60, 90, 150])])
    hw = find(out, "hardware_failure@SW_CAMPUS_4")
    assert hw and hw[0]["score"] == 0.8 and not find(out, "interface_fault@SW_CAMPUS_4")


def test_sustained_cpu_is_device_overload() -> None:
    out = run([("SW_CAMPUS_2", "cpu_pct", [60, 92, 95, 97, 96])])
    assert find(out, "device_overload@SW_CAMPUS_2")[0]["evidence_ids"] == ["EV-002", "EV-003", "EV-004", "EV-005"]
    assert run([("SW_CAMPUS_2", "cpu_pct", [95, 50, 96, 40, 97])]) == []   # spikes are not sustained


def test_queue_drops_with_high_utilization_support_congestion() -> None:
    out = run([("SW_CAMPUS_1", "queue_drops", [1200, 1600, 2100, 2700, 3300]),
               ("HOSTEL_UPLINK_01", "link_utilization_pct", [92, 95, 96, 94, 97])])
    assert find(out, "link_congestion@HOSTEL_UPLINK_01")


def test_weak_wifi_grey_zone_and_strong_signal() -> None:
    weak = run([("HOSTEL_AP_01", "rssi_dbm", [-80, -81, -79, -82, -80]),
                ("HOSTEL_AP_01", "retry_rate_pct", [30, 32, 28, 35, 31])])
    assert find(weak, "weak_wifi@HOSTEL_AP_01")[0]["score"] == 0.8
    grey = run([("HOSTEL_AP_01", "rssi_dbm", [-70, -71, -72, -70, -71]),
                ("HOSTEL_AP_01", "retry_rate_pct", [30, 32, 28, 35, 31])])
    assert find(grey, "weak_wifi@HOSTEL_AP_01")[0]["score"] == 0.5
    assert "grey zone" in find(grey, "weak_wifi@HOSTEL_AP_01")[0]["reason"]
    assert run([("HOSTEL_AP_01", "rssi_dbm", [-70, -71, -72, -70, -71]),
                ("HOSTEL_AP_01", "retry_rate_pct", [3, 4, 2, 3, 3])]) == []
    strong = run([("HOSTEL_AP_01", "rssi_dbm", [-55, -56, -54, -55, -57]),
                  ("HOSTEL_AP_01", "retry_rate_pct", [3, 4, 2, 3, 3])])
    assert find(strong, "healthy@HOSTEL_AP_01", "contradicts")[0]["target_hypothesis"] == "weak_wifi@HOSTEL_AP_01"


def test_counter_critical_is_per_interval_not_window_total() -> None:
    steady = ("DEPT_UPLINK_01", "interface_errors", [0, 40, 80, 120, 160])     # +40 each, 160 total
    assert peak_delta(observations([steady])) == 40
    assert find(run([steady, DEPT_LOSS]), "interface_fault@DEPT_UPLINK_01")[0]["score"] == 0.6   # critical is 100
    burst = ("DEPT_UPLINK_01", "interface_errors", [0, 1, 2, 3, 120])
    assert find(run([burst, DEPT_LOSS]), "interface_fault@DEPT_UPLINK_01")[0]["score"] == 0.8


def test_ambiguous_case_with_real_yaml_thresholds() -> None:
    real = signatures(thresholds={})                 # signatures-1.0.yaml as committed
    out = run([ERRORS_RISING, ("DEPT_UPLINK_01", "link_utilization_pct", [93, 95, 96, 94, 97]), DEPT_LOSS], sig=real)
    assert find(out, "interface_fault@DEPT_UPLINK_01")[0]["score"] == 0.8
    contra = find(out, "interface_fault@DEPT_UPLINK_01", "contradicts")
    assert contra and contra[0]["target_hypothesis"] == "link_congestion@DEPT_UPLINK_01"


def test_null_thresholds_make_checks_unavailable_not_crash() -> None:
    sig = signatures(thresholds={})
    for metric in sig["thresholds"]:
        sig["thresholds"][metric].update(warn=None, critical=None)
    out = run([ERRORS_RISING, ("DEPT_UPLINK_01", "link_utilization_pct", [35, 38, 36, 40, 37]),
               ("SW_CAMPUS_2", "cpu_pct", [99, 99, 99, 99, 99]), DEPT_LOSS], sig=sig)
    assert not find(out, "interface_fault@DEPT_UPLINK_01")          # loss check unavailable: never a pass
    assert not find(out, "interface_fault@DEPT_UPLINK_01", "contradicts")
    assert not find(out, "device_overload@SW_CAMPUS_2")


def test_deterministic_ids() -> None:
    spec = [ERRORS_RISING, ("DEPT_UPLINK_01", "link_utilization_pct", [35, 38, 36, 40, 37]), DEPT_LOSS]
    assert run(spec) == run(spec)
    assert [p["finding_id"] for p in run(spec)] == ["F-INF-01", "F-INF-02"]


# --------------------------------------------------------------------------- Phase 2 review (R4, C7)

def test_errors_without_downstream_loss_are_not_a_fault() -> None:
    """Rising errors alone are background noise; the signature also needs downstream loss."""
    out = run([ERRORS_RISING, ("SW_CAMPUS_8", "packet_loss_pct", [0, 0, 0.2, 0, 0])])
    assert not find(out, "interface_fault@DEPT_UPLINK_01")
    assert not find(out, "interface_fault@DEPT_UPLINK_01", "contradicts")


def test_background_counter_ticking_is_not_a_fault() -> None:
    """+0..2 errors and +0..5 drops per minute on a healthy, lossless network (real YAML)."""
    real = signatures(thresholds={})
    out = run([("DEPT_UPLINK_01", "interface_errors", [12, 14, 15, 15, 17]),
               ("HOSTEL_UPLINK_01", "interface_errors", [12, 13, 15, 16, 18]),
               ("SW_CAMPUS_1", "queue_drops", [310, 313, 318, 318, 322]),
               ("HOSTEL_UPLINK_01", "link_utilization_pct", [35, 38, 36, 40, 37]),
               ("SW_CAMPUS_3", "packet_loss_pct", [0, 0, 0, 0, 0])], sig=real)
    assert not [p for p in out if p["stance"] == "supports" and not p["hypothesis"].startswith("healthy")]


def test_weak_rssi_without_retries_is_not_weak_wifi() -> None:
    out = run([("HOSTEL_AP_01", "rssi_dbm", [-80, -81, -79, -82, -80]),
               ("HOSTEL_AP_01", "retry_rate_pct", [3, 4, 2, 3, 3])])
    assert not find(out, "weak_wifi@HOSTEL_AP_01")
