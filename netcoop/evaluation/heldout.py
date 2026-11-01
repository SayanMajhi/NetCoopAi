"""Held-out incident generator for Phase 4 (ARCHITECTURE §7, Phase 3 re-review P4-0/P4-3/P4-4).

Written and committed before any held-out result was looked at. It never imports the agents,
the decision code or labels.json: every label below follows from the fault the generator itself
overlays, by the rules stated in LABEL_RULES. Thresholds, weights and gates stay frozen at
`ed288c2`; nothing here reads signatures-1.0.yaml.

Differences from scenarios/dev (P4-4):
- seeds: case i of run seed S uses Random(S * 10_000 + i); dev uses 101-116;
- time: each case has its own window end on 2026-11-02..09 with an odd second offset (dev: 10:05:00Z on 11-01);
- positions: congestion on access links (ACC_LINK_03..10), interface faults on links dev never faults
  (ACC_LINK_03..06/08..10, DEPT_UPLINK_01), weak Wi-Fi on DEPT_AP_01, DNS/DHCP clients drawn from all
  access switches, client probes on a random access switch per zone;
- metric mixes: congestion corroborated by latency only, drops only, or both; interface faults seen through
  errors only, flaps only, or both; one-source DNS/DHCP + reachability (P4-3); busy-but-healthy links (P4-0);
  stale or missing key telemetry.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

SWITCH, PROBE, WLC, ICMP, ON_DEMAND = "switch-telemetry", "client-probe", "wlc-telemetry", "icmp-monitor", "on-demand-probe"
UPLINKS = {"HOSTEL_UPLINK_01": "SW_CAMPUS_1", "DEPT_UPLINK_01": "SW_CAMPUS_2"}
ZONES = {"hostel": [f"SW_CAMPUS_{n}" for n in range(3, 7)], "department": [f"SW_CAMPUS_{n}" for n in range(7, 11)]}
ACCESS = ZONES["hostel"] + ZONES["department"]
ACCESS_LINK = {sw: f"ACC_LINK_{sw.rsplit('_', 1)[1].zfill(2)}" for sw in ACCESS}
PARENT = {sw: ("SW_CAMPUS_1" if sw in ZONES["hostel"] else "SW_CAMPUS_2") for sw in ACCESS}
APS = {"HOSTEL_AP_01": ("HOSTEL_AP_LINK_01", "SW_CAMPUS_3"), "DEPT_AP_01": ("DEPT_AP_LINK_01", "SW_CAMPUS_7")}
LABEL = {"SW_CAMPUS_3": "Hostel block A", "SW_CAMPUS_4": "Hostel block B", "SW_CAMPUS_5": "Hostel block C",
         "SW_CAMPUS_6": "Hostel block D", "SW_CAMPUS_7": "CSE department", "SW_CAMPUS_8": "EE department",
         "SW_CAMPUS_9": "Lecture hall", "SW_CAMPUS_10": "Library"}

LABEL_RULES = {
    "healthy": "background only, every service/path check passes -> healthy",
    "busy_healthy": "one link 80-93 % utilization (spiky or steady), latency normal, drops only background "
                    "growth (< 10/interval) -> not a fault; any diagnosed/probable answer is a false diagnosis",
    "link_congestion": "utilization sustained >= 91 % on the link; corroborated by probe latency (second source) "
                       "-> diagnosed; by queue drops only (same source as utilization) -> probable",
    "interface_fault": "errors and/or flaps rising >= 15/interval on the link plus downstream loss 3-8 % from a "
                       "probe -> diagnosed",
    "weak_wifi": "AP RSSI -86..-77 dBm and retries 25-40 % with a clean AP uplink -> diagnosed",
    "dns_failure": "2-4 clients fail DNS, server reachable; reachability from the ICMP poller -> diagnosed; "
                   "DNS and reachability from the same client probe (P4-3) -> probable",
    "dhcp_failure": "as dns_failure, for DHCP",
    "stale_telemetry": "a congestion or interface fault whose key series is missing or 20+ minutes old "
                       "-> insufficient_evidence",
}
CLASSES = list(LABEL_RULES)
NO_FAULT = {"healthy", "busy_healthy", "stale_telemetry"}


@dataclass
class Case:
    name: str
    cls: str
    seed: int
    fixture: dict[str, Any]
    label: dict[str, Any]             # status, cause, affected_ids, variant, why
    tags: list[str] = field(default_factory=list)


class _Build:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        day = self.rng.randint(2, 9)
        self.end = datetime(2026, 11, day, self.rng.randint(0, 23), self.rng.randint(5, 59),
                            self.rng.choice([7, 13, 19, 41, 47, 53]), tzinfo=timezone.utc)
        self.times = [self.end - timedelta(minutes=4 - i, seconds=self.rng.randint(1, 20)) for i in range(5)]
        self.series: dict[tuple[str, str, str], dict[str, list[Any]]] = {}
        self.stale: set[tuple[str, str]] = set()
        self.on_query: list[dict[str, Any]] = []

    def u(self, lo: float, hi: float, digits: int = 1) -> list[float]:
        return [round(self.rng.uniform(lo, hi), digits) for _ in range(5)]

    def counter(self, lo: int, hi: int, start: int | None = None) -> list[int]:
        v = [start if start is not None else self.rng.randint(5, 900)]
        for _ in range(4):
            v.append(v[-1] + self.rng.randint(lo, hi))
        return v

    def set(self, kind: str, target: str, source: str, metric: str, values: list[Any]) -> None:
        self.series.setdefault((kind, target, source), {})[metric] = list(values)

    def get(self, kind: str, target: str, source: str, metric: str) -> list[Any] | None:
        return self.series.get((kind, target, source), {}).get(metric)

    def probe_query(self, kind: str, target: str, metric: str, values: list[Any]) -> None:
        for i, v in enumerate(values):
            t = self.end - timedelta(seconds=20 - 10 * i)
            self.on_query.append({"timestamp": _iso(t), kind: target, "source_id": ON_DEMAND, "metrics": {metric: v}})

    def records(self) -> list[dict[str, Any]]:
        out = []
        for (kind, target, source), metrics in sorted(self.series.items()):
            for i, t in enumerate(self.times):
                when = t - timedelta(minutes=25) if (target, source) in self.stale else t
                out.append({"timestamp": _iso(when), kind: target, "source_id": source,
                            "metrics": {m: v[i] for m, v in sorted(metrics.items())}})
        return out


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _background(b: _Build, *, reach_source: str = ICMP) -> dict[str, Any]:
    """A healthy campus with its own load level, probe positions and DNS/DHCP clients."""
    load = b.rng.uniform(15, 55)
    for link in UPLINKS:
        util = b.u(max(5, load - 8), load + 8)
        b.set("link_id", link, SWITCH, "link_utilization_pct", util)
        b.set("link_id", link, SWITCH, "throughput_mbps", [round(x * 10, 1) for x in util])
        b.set("link_id", link, SWITCH, "interface_errors", b.counter(0, 2))
    for sw in UPLINKS.values():
        b.set("device_id", sw, SWITCH, "cpu_utilization_pct", b.u(12, 40))
        b.set("device_id", sw, SWITCH, "memory_pct", b.u(35, 58))
        b.set("device_id", sw, SWITCH, "queue_drops", b.counter(0, 6))
    probes = [b.rng.choice(ZONES["hostel"]), b.rng.choice(ZONES["department"])]
    for sw in probes:
        b.set("device_id", sw, PROBE, "latency_ms", b.u(4, 18))
        b.set("device_id", sw, PROBE, "jitter_ms", b.u(0.5, 4))
        b.set("device_id", sw, PROBE, "packet_loss_pct", [0.0] * 5)
    clients = sorted(b.rng.sample(ACCESS, 4))
    for sw in clients:
        b.set("device_id", sw, PROBE, "dns_success", [True] * 5)
        b.set("device_id", sw, PROBE, "dhcp_success", [True] * 5)
    for sw in ACCESS:
        b.set("device_id", sw, reach_source, "reachability", [True] * 5)
    b.set("device_id", "SRV_DNS_DHCP_01", reach_source, "reachability", [True] * 5)
    for ap, (link, _) in APS.items():
        b.set("device_id", ap, WLC, "rssi_dbm", b.u(-62, -50, 0))
        b.set("device_id", ap, WLC, "retry_rate_pct", b.u(2, 8))
        b.set("link_id", link, SWITCH, "link_utilization_pct", b.u(8, 30))
        b.set("link_id", link, SWITCH, "packet_loss_pct", [0.0] * 5)
    return {"probes": probes, "clients": clients}


def _wrap(b: _Build, incident: str, complaint: str) -> dict[str, Any]:
    fx = {"incident_id": incident, "window_end": _iso(b.end), "complaint": complaint, "records": b.records()}
    if b.on_query:
        fx["on_query"] = b.on_query
    return fx


def _label(status: str, cause: str | None, affected: list[str], variant: str, why: str) -> dict[str, Any]:
    return {"status": status, "cause": cause, "affected_ids": affected, "variant": variant, "why": why}


# --------------------------------------------------------------------------- classes


def _healthy(b: _Build, inc: str) -> tuple[dict, dict]:
    _background(b)
    sw = b.rng.choice(ACCESS)
    complaint = b.rng.choice([f"{LABEL[sw]} users say the internet is slow", "Routine health check",
                              f"Check {sw} after maintenance", "Students report intermittent slowness"])
    variant = "complaint names nothing" if complaint.startswith(("Routine", "Students")) else "complaint names an area"
    return _wrap(b, inc, complaint), _label("healthy", None, [], variant, LABEL_RULES["healthy"])


def _busy_healthy(b: _Build, inc: str) -> tuple[dict, dict]:
    _background(b)
    link = b.rng.choice(list(UPLINKS) + [ACCESS_LINK[s] for s in ACCESS])
    if b.rng.random() < 0.5:
        variant = "spiky 80-93 %"
        util = [round(b.rng.uniform(88, 93) if i % 2 == 0 else b.rng.uniform(80, 86), 1) for i in range(5)]
    else:
        variant = "steady 84-93 %"
        util = b.u(84, 93)
    b.set("link_id", link, SWITCH, "link_utilization_pct", util)
    b.set("link_id", link, SWITCH, "throughput_mbps", [round(x * 10, 1) for x in util])
    zone = "hostel" if link == "HOSTEL_UPLINK_01" or link in [ACCESS_LINK[s] for s in ZONES["hostel"]] else "department"
    return (_wrap(b, inc, f"{zone.capitalize()} feels a bit slow at peak hours"),
            _label("insufficient_evidence", None, [], variant, LABEL_RULES["busy_healthy"]))


def _congestion(b: _Build, inc: str) -> tuple[dict, dict]:
    _background(b)
    sw = b.rng.choice(ACCESS)
    link, upstream = ACCESS_LINK[sw], PARENT[sw]
    util = b.u(91, 99)
    b.set("link_id", link, SWITCH, "link_utilization_pct", util)
    b.set("link_id", link, SWITCH, "throughput_mbps", [round(x * 10, 1) for x in util])
    mix = b.rng.choice(["latency only", "drops only", "latency and drops"])
    if "drops" in mix:
        b.set("device_id", sw, SWITCH, "queue_drops", b.counter(60, 400))
    if "latency" in mix:
        b.set("device_id", sw, PROBE, "latency_ms", b.u(150, 350))
        b.set("device_id", sw, PROBE, "jitter_ms", b.u(20, 60))
        b.set("device_id", sw, PROBE, "packet_loss_pct", b.u(0.3, 2.0, 2))
    if b.rng.random() < 0.7:
        b.probe_query("link_id", link, "link_utilization_pct", [round(b.rng.uniform(92, 99), 1) for _ in range(3)])
    status = "diagnosed" if "latency" in mix else "probable"
    return (_wrap(b, inc, f"{LABEL[sw]} is very slow since this morning"),
            _label(status, "link_congestion", [link, sw], mix, LABEL_RULES["link_congestion"]))


def _interface_fault(b: _Build, inc: str) -> tuple[dict, dict]:
    _background(b)
    candidates = [s for s in ACCESS if s != "SW_CAMPUS_7"] + ["DEPT_UPLINK_01"]
    pick = b.rng.choice(candidates)
    if pick == "DEPT_UPLINK_01":
        link, sw, victim = pick, "SW_CAMPUS_2", b.rng.choice(ZONES["department"])
    else:
        link, sw, victim = ACCESS_LINK[pick], pick, pick
    mix = b.rng.choice(["errors only", "flaps only", "errors and flaps"])
    b.set("link_id", link, SWITCH, "interface_errors", b.counter(15, 60) if "errors" in mix else b.counter(0, 2))
    if "flaps" in mix:
        b.set("link_id", link, SWITCH, "link_flap_count", b.counter(15, 30, start=b.rng.randint(0, 5)))
    if b.get("link_id", link, SWITCH, "link_utilization_pct") is None:
        b.set("link_id", link, SWITCH, "link_utilization_pct", b.u(10, 35))
    b.set("device_id", victim, PROBE, "packet_loss_pct", b.u(3, 8, 2))
    b.set("device_id", victim, PROBE, "latency_ms", b.u(20, 60))
    if "errors" in mix and b.rng.random() < 0.7:
        last = b.get("link_id", link, SWITCH, "interface_errors")[-1]
        b.probe_query("link_id", link, "interface_errors", [last + 5 * i for i in range(3)])
    return (_wrap(b, inc, f"{LABEL[victim]} wired connections keep dropping"),
            _label("diagnosed", "interface_fault", [link, sw], mix, LABEL_RULES["interface_fault"]))


def _weak_wifi(b: _Build, inc: str) -> tuple[dict, dict]:
    _background(b)
    ap = "DEPT_AP_01"
    b.set("device_id", ap, WLC, "rssi_dbm", b.u(-86, -77, 0))
    b.set("device_id", ap, WLC, "retry_rate_pct", b.u(25, 40))
    b.set("device_id", ap, PROBE, "latency_ms", b.u(50, 120))
    b.set("device_id", ap, PROBE, "packet_loss_pct", b.u(1.5, 5, 2))
    if b.rng.random() < 0.7:
        b.probe_query("device_id", ap, "rssi_dbm", [float(b.rng.randint(-85, -78)) for _ in range(3)])
    return (_wrap(b, inc, "CSE department Wi-Fi keeps disconnecting"),
            _label("diagnosed", "weak_wifi", [ap], "DEPT_AP_01", LABEL_RULES["weak_wifi"]))


def _service(metric: str, cause: str) -> Callable[[_Build, str], tuple[dict, dict]]:
    def build(b: _Build, inc: str) -> tuple[dict, dict]:
        one_source = b.rng.random() < 0.4
        ctx = _background(b, reach_source=PROBE if one_source else ICMP)
        failing = b.rng.sample(ctx["clients"], b.rng.randint(2, 4))
        for sw in failing:
            b.set("device_id", sw, PROBE, metric, [False] * 5)
        complaint = ("Websites do not open but ping works" if cause == "dns_failure"
                     else "Devices joining the network get no IP address")
        variant = "one source (P4-3)" if one_source else "two sources"
        return (_wrap(b, inc, complaint),
                _label("probable" if one_source else "diagnosed", cause, ["SRV_DNS_DHCP_01"], variant,
                       LABEL_RULES[cause]))
    build.__name__ = f"_{cause}"
    return build


def _stale_telemetry(b: _Build, inc: str) -> tuple[dict, dict]:
    _background(b)
    sw = b.rng.choice(ACCESS)
    link = ACCESS_LINK[sw]
    if b.rng.random() < 0.5:
        variant = "congestion, utilization missing"
        b.set("link_id", link, SWITCH, "link_utilization_pct", [None] * 5)
        b.set("device_id", sw, SWITCH, "queue_drops", b.counter(60, 400))
        b.set("device_id", sw, PROBE, "latency_ms", b.u(150, 350))
        b.stale.add((sw, PROBE))
    else:
        variant = "interface fault, switch telemetry stale"
        b.set("link_id", link, SWITCH, "interface_errors", b.counter(15, 60))
        b.set("device_id", sw, PROBE, "packet_loss_pct", b.u(3, 8, 2))
        b.stale.update({(link, SWITCH), (sw, PROBE)})
    return (_wrap(b, inc, f"{LABEL[sw]} slow; the monitoring collector was down"),
            _label("insufficient_evidence", None, [], variant, LABEL_RULES["stale_telemetry"]))


BUILDERS: dict[str, Callable[[_Build, str], tuple[dict, dict]]] = {
    "healthy": _healthy, "busy_healthy": _busy_healthy, "link_congestion": _congestion,
    "interface_fault": _interface_fault, "weak_wifi": _weak_wifi,
    "dns_failure": _service("dns_success", "dns_failure"), "dhcp_failure": _service("dhcp_success", "dhcp_failure"),
    "stale_telemetry": _stale_telemetry,
}
assert list(BUILDERS) == CLASSES


def generate(n: int, seed: int) -> list[Case]:
    """n cases, classes in round-robin order (balanced counts), each from its own Random(seed*10_000 + i)."""
    cases = []
    for i in range(n):
        cls = CLASSES[i % len(CLASSES)]
        case_seed = seed * 10_000 + i
        b = _Build(case_seed)
        incident = f"INC-H{seed}-{i:03d}"
        fixture, label = BUILDERS[cls](b, incident)
        cases.append(Case(name=f"{cls}-{i:03d}", cls=cls, seed=case_seed, fixture=fixture, label=label,
                          tags=[label["variant"]]))
    return cases
