"""Write scenarios/dev/*.json — one labelled incident per class in scenarios/labels.json.

    python scenarios/build_dev_fixtures.py            # rewrite scenarios/dev/
    python scenarios/build_dev_fixtures.py --out DIR  # write elsewhere (determinism test)

Every scenario is a 5-minute window (window_end 10:05:00Z, 5 samples a minute apart)
over a healthy campus background, with one fault overlaid. Values are realistic
for a 1 Gbps campus and were chosen from the fault description, not from the
thresholds in signatures-1.0.yaml; the expected answers live only in labels.json.
Three independent sources report: switch telemetry, client probes and the wireless
controller. Output is deterministic (fixed seed, fixed timestamps).
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any, Callable

OUT = Path(__file__).resolve().parent / "dev"
WINDOW_END = "2026-11-01T10:05:00Z"
TIMES = [f"2026-11-01T10:0{m}:30Z" for m in range(5)]
STALE_TIMES = [f"2026-11-01T09:4{m}:30Z" for m in range(5)]   # 20+ minutes before the window

SWITCH, PROBE, WLC = "switch-telemetry", "client-probe", "wlc-telemetry"
ICMP = "icmp-monitor"   # reachability comes from the NMS ping poller, not from the synthetic DNS/DHCP client probes
UPLINKS = {"HOSTEL_UPLINK_01": "SW_CAMPUS_1", "DEPT_UPLINK_01": "SW_CAMPUS_2"}
PROBED = ["SW_CAMPUS_3", "SW_CAMPUS_8"]          # one client probe per zone
CHECKED = ["SW_CAMPUS_3", "SW_CAMPUS_4", "SW_CAMPUS_8", "SW_CAMPUS_9"]   # DNS/DHCP clients
ACCESS = [f"SW_CAMPUS_{n}" for n in range(3, 11)]                        # reachability probe on every access switch

Series = list[Any]


class Scenario:
    """Records keyed by (target, source); each metric is a 5-sample series."""

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.series: dict[tuple[str, str, str], dict[str, Series]] = {}

    def noisy(self, low: float, high: float, digits: int = 1) -> Series:
        return [round(self.rng.uniform(low, high), digits) for _ in TIMES]

    def counter(self, start: int, low: int, high: int) -> Series:
        """A cumulative counter that grows by low..high per minute (healthy links still tick)."""
        values = [start]
        for _ in TIMES[1:]:
            values.append(values[-1] + self.rng.randint(low, high))
        return values

    def set(self, kind: str, target: str, source: str, metric: str, values: Series) -> None:
        assert len(values) == len(TIMES)
        self.series.setdefault((kind, target, source), {})[metric] = list(values)

    def drop(self, kind: str, target: str, source: str, metric: str) -> None:
        self.series.get((kind, target, source), {}).pop(metric, None)

    def records(self, times: list[str] = TIMES, stale: set[tuple[str, str]] = frozenset()) -> list[dict[str, Any]]:
        out = []
        for (kind, target, source), metrics in sorted(self.series.items()):
            for i, t in enumerate(times):
                when = STALE_TIMES[i] if (target, source) in stale else t
                out.append({"timestamp": when, kind: target, "source_id": source,
                            "metrics": {m: v[i] for m, v in sorted(metrics.items())}})
        return out


def background(seed: int) -> Scenario:
    """A healthy campus: moderate load, slowly ticking counters, passing service and path checks."""
    s = Scenario(seed)
    for link in UPLINKS:
        util = s.noisy(28, 42)
        s.set("link_id", link, SWITCH, "link_utilization_pct", util)
        s.set("link_id", link, SWITCH, "throughput_mbps", [round(u * 10, 1) for u in util])
        s.set("link_id", link, SWITCH, "interface_errors", s.counter(12, 0, 2))
    for switch in UPLINKS.values():
        s.set("device_id", switch, SWITCH, "cpu_utilization_pct", s.noisy(18, 34))
        s.set("device_id", switch, SWITCH, "memory_pct", s.noisy(40, 52))
        s.set("device_id", switch, SWITCH, "queue_drops", s.counter(310, 0, 5))
    for device in PROBED:
        s.set("device_id", device, PROBE, "latency_ms", s.noisy(6, 14))
        s.set("device_id", device, PROBE, "jitter_ms", s.noisy(1, 3))
        s.set("device_id", device, PROBE, "packet_loss_pct", [0.0] * 5)
    for device in CHECKED:
        s.set("device_id", device, PROBE, "dns_success", [True] * 5)
        s.set("device_id", device, PROBE, "dhcp_success", [True] * 5)
    for device in ACCESS:
        s.set("device_id", device, ICMP, "reachability", [True] * 5)
    s.set("device_id", "SRV_DNS_DHCP_01", ICMP, "reachability", [True] * 5)
    s.set("device_id", "HOSTEL_AP_01", WLC, "rssi_dbm", s.noisy(-60, -54, 0))
    s.set("device_id", "HOSTEL_AP_01", WLC, "retry_rate_pct", s.noisy(3, 6))
    s.set("link_id", "HOSTEL_AP_LINK_01", SWITCH, "link_utilization_pct", s.noisy(15, 25))
    s.set("link_id", "HOSTEL_AP_LINK_01", SWITCH, "packet_loss_pct", [0.0] * 5)
    return s


def wrap(incident: str, complaint: str, records: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"incident_id": incident, "window_end": WINDOW_END, "complaint": complaint, "records": records, **extra}


def congestion_overlay(s: Scenario, link: str, switch: str, probe: str) -> None:
    util = s.noisy(93, 98)
    s.set("link_id", link, SWITCH, "link_utilization_pct", util)
    s.set("link_id", link, SWITCH, "throughput_mbps", [round(u * 10, 1) for u in util])
    s.set("device_id", switch, SWITCH, "queue_drops", [1040, 1480, 2010, 2620, 3190])
    s.set("device_id", probe, PROBE, "latency_ms", s.noisy(190, 320))
    s.set("device_id", probe, PROBE, "jitter_ms", s.noisy(25, 60))
    s.set("device_id", probe, PROBE, "packet_loss_pct", s.noisy(0.6, 2.2, 2))


ON_DEMAND = "on-demand-probe"
PROBE_TIMES = ["2026-11-01T10:04:40Z", "2026-11-01T10:04:50Z", "2026-11-01T10:05:00Z"]


def on_query(*probes: tuple[str, str, str, list[Any]]) -> list[dict[str, Any]]:
    """Held-back on-demand probe samples (kind, target, metric, 3 values 10 s apart at the end of
    the window). An agent releases them only when Correlation QUERYs that measurement, so the
    RESPONSE carries new evidence instead of re-reading the window."""
    return [{"timestamp": t, kind: target, "source_id": ON_DEMAND, "metrics": {metric: values[i]}}
            for kind, target, metric, values in probes for i, t in enumerate(PROBE_TIMES)]


def finding_message(incident: str, message_id: str, hypothesis: str, stance: str = "supports") -> dict[str, Any]:
    return {"protocol": "netcoop/1.0", "message_id": message_id, "incident_id": incident, "in_reply_to": None,
            "sender": "external_monitor", "recipient": "correlation_agent", "type": "FINDING",
            "timestamp": WINDOW_END,
            "payload": {"finding_id": f"F-EXT-{message_id[-2:]}", "hypothesis": hypothesis, "stance": stance,
                        "target_hypothesis": hypothesis, "evidence_ids": ["EV-001"],
                        "reason": "external NMS alarm"}, "extra": {}}


# --------------------------------------------------------------------------- scenarios


def normal() -> dict[str, Any]:
    return wrap("INC-0101", "Routine health check", background(101).records())


def false_report() -> dict[str, Any]:
    s = background(102)
    s.set("device_id", "SW_CAMPUS_10", PROBE, "latency_ms", s.noisy(7, 13))
    s.set("device_id", "SW_CAMPUS_10", PROBE, "packet_loss_pct", [0.0] * 5)
    s.set("device_id", "SW_CAMPUS_10", PROBE, "dns_success", [True] * 5)
    return wrap("INC-0102", "Library users say the internet is slow", s.records())


def congestion() -> dict[str, Any]:
    s = background(103)
    congestion_overlay(s, "HOSTEL_UPLINK_01", "SW_CAMPUS_1", "SW_CAMPUS_3")
    return wrap("INC-0103", "Hostel blocks report slow internet in the evening", s.records(),
                on_query=on_query(("link_id", "HOSTEL_UPLINK_01", "link_utilization_pct", [95.8, 96.9, 94.7])))


def weak_wifi() -> dict[str, Any]:
    s = background(104)
    s.set("device_id", "HOSTEL_AP_01", WLC, "rssi_dbm", s.noisy(-83, -78, 0))
    s.set("device_id", "HOSTEL_AP_01", WLC, "retry_rate_pct", s.noisy(27, 36))
    s.set("device_id", "HOSTEL_AP_01", PROBE, "latency_ms", s.noisy(60, 110))
    s.set("device_id", "HOSTEL_AP_01", PROBE, "packet_loss_pct", s.noisy(2, 4.5, 2))
    return wrap("INC-0104", "Hostel block A Wi-Fi keeps dropping", s.records(),
                on_query=on_query(("device_id", "HOSTEL_AP_01", "rssi_dbm", [-80.0, -81.0, -79.0])))


def interface_fault() -> dict[str, Any]:
    s = background(105)
    s.set("link_id", "ACC_LINK_07", SWITCH, "interface_errors", [140, 172, 211, 246, 289])
    s.set("link_id", "ACC_LINK_07", SWITCH, "link_flap_count", [3, 3, 4, 4, 5])
    s.set("link_id", "ACC_LINK_07", SWITCH, "link_utilization_pct", s.noisy(18, 30))
    s.set("device_id", "SW_CAMPUS_7", PROBE, "packet_loss_pct", s.noisy(4, 7, 2))
    s.set("device_id", "SW_CAMPUS_7", PROBE, "latency_ms", s.noisy(25, 55))
    return wrap("INC-0105", "CSE department wired connections are flaky", s.records(),
                on_query=on_query(("link_id", "ACC_LINK_07", "interface_errors", [289, 295, 301])))


def dns_failure() -> dict[str, Any]:
    s = background(106)
    for device in ("SW_CAMPUS_3", "SW_CAMPUS_8", "SW_CAMPUS_9"):
        s.set("device_id", device, PROBE, "dns_success", [False] * 5)
    return wrap("INC-0106", "Websites do not open but ping works", s.records())


def dhcp_failure() -> dict[str, Any]:
    s = background(107)
    for device in ("SW_CAMPUS_4", "SW_CAMPUS_8", "SW_CAMPUS_9"):
        s.set("device_id", device, PROBE, "dhcp_success", [False] * 5)
    return wrap("INC-0107", "New laptops get no IP address", s.records())


def ambiguous_congestion_errors() -> dict[str, Any]:
    s = background(108)
    congestion_overlay(s, "DEPT_UPLINK_01", "SW_CAMPUS_2", "SW_CAMPUS_8")
    s.set("link_id", "DEPT_UPLINK_01", SWITCH, "interface_errors", [12, 17, 21, 26, 30])
    return wrap("INC-0108", "Department network slow; switch logs show CRC errors", s.records(),
                on_query=on_query(("link_id", "DEPT_UPLINK_01", "link_utilization_pct", [95.1, 96.4, 94.8]),
                                  ("link_id", "DEPT_UPLINK_01", "interface_errors", [30, 31, 32])))


def missing_stale() -> dict[str, Any]:
    s = background(109)
    congestion_overlay(s, "HOSTEL_UPLINK_01", "SW_CAMPUS_1", "SW_CAMPUS_3")
    s.set("link_id", "HOSTEL_UPLINK_01", SWITCH, "link_utilization_pct", [None] * 5)
    s.set("link_id", "HOSTEL_UPLINK_01", SWITCH, "throughput_mbps", [None] * 5)
    stale = {("SW_CAMPUS_1", SWITCH), ("SW_CAMPUS_3", PROBE)}
    return wrap("INC-0109", "Hostel slow; monitoring server was restarted", s.records(stale=stale))


def unit_mismatch() -> dict[str, Any]:
    s = background(110)
    congestion_overlay(s, "DEPT_UPLINK_01", "SW_CAMPUS_2", "SW_CAMPUS_8")
    util = s.series[("link_id", "DEPT_UPLINK_01", SWITCH)]
    kbps = [round(u * 10_000) for u in util.pop("link_utilization_pct")]   # 1000 Mbps link, sent in kbps
    util.pop("throughput_mbps")
    s.set("link_id", "DEPT_UPLINK_01", SWITCH, "throughput_kbps", kbps)
    probe = s.series[("device_id", "SW_CAMPUS_8", PROBE)]
    probe["rtt_sec"] = [round(v / 1000, 3) for v in probe.pop("latency_ms")]
    return wrap("INC-0110", "Department slow; data from a legacy collector", s.records(),
                on_query=on_query(("link_id", "DEPT_UPLINK_01", "link_utilization_pct", [95.1, 96.4, 94.8])))


def counter_reset() -> dict[str, Any]:
    s = background(111)
    s.set("link_id", "ACC_LINK_05", SWITCH, "interface_errors", [518, 520, 0, 1, 2])   # reboot, then normal ticking
    s.set("device_id", "SW_CAMPUS_5", PROBE, "packet_loss_pct", [0.0] * 5)
    s.set("device_id", "SW_CAMPUS_5", ICMP, "reachability", [True] * 5)
    return wrap("INC-0111", "Switch SW_CAMPUS_5 rebooted overnight; check it is fine", s.records())


def schema_0_9() -> dict[str, Any]:
    s = background(112)
    s.set("link_id", "HOSTEL_UPLINK_01", SWITCH, "interface_errors", [40, 66, 95, 121, 150])
    s.set("device_id", "SW_CAMPUS_3", PROBE, "packet_loss_pct", s.noisy(3.5, 6, 2))
    s.set("device_id", "SW_CAMPUS_4", PROBE, "packet_loss_pct", s.noisy(3.5, 6, 2))
    legacy = {"protocol": "netcoop/0.9", "msg_id": "INC-0112-LEG-01", "incident": "INC-0112",
              "from": "legacy_switch_monitor", "to": "correlation_agent", "kind": "finding", "ts": 1793527500,
              "body": {"id": "F-LEG-01", "cause": "interface_fault", "target": "HOSTEL_UPLINK_01",
                       "supports": True, "evidence": ["EV-001"], "why": "CRC errors rising on uplink"}}
    return wrap("INC-0112", "Hostel packet loss; legacy monitor raised an alarm", s.records(), messages=[legacy],
                on_query=on_query(("link_id", "HOSTEL_UPLINK_01", "interface_errors", [150, 155, 159])))


def _sparse(seed: int) -> Scenario:
    """Too little telemetry to call anything healthy or faulty."""
    s = Scenario(seed)
    s.set("device_id", "SW_CAMPUS_1", SWITCH, "cpu_utilization_pct", s.noisy(20, 30))
    s.set("device_id", "SW_CAMPUS_3", PROBE, "latency_ms", s.noisy(70, 110))
    return s


def unsupported_2_x() -> dict[str, Any]:
    msg = finding_message("INC-0113", "INC-0113-EXT-01", "link_congestion@HOSTEL_UPLINK_01")
    msg["protocol"] = "netcoop/2.0"
    return wrap("INC-0113", "Alarm from a newer monitoring agent", _sparse(113).records(), messages=[msg])


def missing_message_field() -> dict[str, Any]:
    msg = finding_message("INC-0114", "INC-0114-EXT-01", "link_congestion@HOSTEL_UPLINK_01")
    del msg["payload"]["stance"]
    return wrap("INC-0114", "Alarm with an incomplete message", _sparse(114).records(), messages=[msg])


def duplicate_message() -> dict[str, Any]:
    s = background(115)
    congestion_overlay(s, "HOSTEL_UPLINK_01", "SW_CAMPUS_1", "SW_CAMPUS_3")
    msg = finding_message("INC-0115", "INC-0115-EXT-01", "link_congestion@HOSTEL_UPLINK_01")
    return wrap("INC-0115", "Hostel slow; the NMS alarm was sent twice", s.records(), messages=[msg, msg],
                on_query=on_query(("link_id", "HOSTEL_UPLINK_01", "link_utilization_pct", [95.8, 96.9, 94.7])))


def correlation_timeout() -> dict[str, Any]:
    s = background(116)
    congestion_overlay(s, "HOSTEL_UPLINK_01", "SW_CAMPUS_1", "SW_CAMPUS_3")
    return wrap("INC-0116", "Hostel slow; correlation service overloaded", s.records(),
                inject={"correlation": "timeout"})


BUILDERS: dict[str, Callable[[], dict[str, Any]]] = {f.__name__: f for f in [
    normal, false_report, congestion, weak_wifi, interface_fault, dns_failure, dhcp_failure,
    ambiguous_congestion_errors, missing_stale, unit_mismatch, counter_reset, schema_0_9,
    unsupported_2_x, missing_message_field, duplicate_message, correlation_timeout,
]}
MALFORMED_TEXT = '{"incident_id": "INC-0117", "window_end": "2026-11-01T10:05:00Z", "records": [\n  {"timestamp": "2026-11-01T10:00:30Z", "device_id": "SW_CAMPUS_1", "metrics": {"cpu_utilization_pct": 31.2}\n'


def build(out: Path = OUT) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, builder in BUILDERS.items():
        path = out / f"{name}.json"
        path.write_text(json.dumps(builder(), indent=1) + "\n", encoding="utf-8", newline="\n")
        written.append(path)
    malformed = out / "malformed_input.json"           # deliberately truncated JSON
    malformed.write_text(MALFORMED_TEXT, encoding="utf-8", newline="\n")
    written.append(malformed)
    return written


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=OUT)
    for path in build(parser.parse_args().out):
        print(path)
