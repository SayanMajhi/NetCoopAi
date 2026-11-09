"""Inputs built to make the system overclaim or crash. Each case reproduces a failure found in an
external audit; none of them is a scenario the held-out generator produces."""
from __future__ import annotations

import copy
import json
import random
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import netcoop.orchestrator.run as pipeline
from connectivity_service.app import create_app
from netcoop.agents.correlation import CorrelationAgent
from netcoop.cli import main
from netcoop.orchestrator.bus import MessageBus
from netcoop.orchestrator.run import default_components, load_signatures, load_topology, run_incident
from netcoop.orchestrator.transport import ConnectivityReply
from netcoop.protocol.models import Report

ROOT = Path(__file__).resolve().parents[1]
TOPOLOGY, SIGNATURES = load_topology(), load_signatures()
UPLINK = "HOSTEL_UPLINK_01"


def scenario(name: str) -> dict:
    return json.loads((ROOT / "scenarios" / "dev" / f"{name}.json").read_text(encoding="utf-8"))


def wire_bundle() -> dict:
    return json.loads((ROOT / "examples" / "wire" / "bundle.json").read_text(encoding="utf-8"))


def run(fx, components=None, signatures=None):
    return run_incident(fx, TOPOLOGY, signatures or SIGNATURES,
                        components=components or default_components(connectivity="local"))


def errors(result, code):
    return [m for m in result.messages if m.type == "ERROR" and m.payload["code"] == code]


def is_util(record) -> bool:
    return record.get("link_id") == UPLINK and "link_utilization_pct" in record["metrics"]


def valid_finding(fx: dict, **changes) -> dict:
    target = next(r for r in fx["records"] if is_util(r))
    message = {
        "protocol": "netcoop/1.0", "message_id": "X-1", "incident_id": fx["incident_id"], "in_reply_to": None,
        "sender": "infrastructure_agent", "recipient": "correlation_agent", "type": "FINDING",
        "timestamp": target["timestamp"],
        "payload": {"finding_id": "F-X-1", "hypothesis": f"device_overload@{UPLINK}", "stance": "supports",
                    "target_hypothesis": f"device_overload@{UPLINK}", "evidence_ids": ["EV-001"], "reason": "forged"},
    }
    message.update(changes)
    return message


# --------------------------------------------------------------------------- replayed telemetry


def replayed() -> dict:
    fx = copy.deepcopy(scenario("congestion"))
    fx.pop("on_query", None)
    first = next(r for r in fx["records"] if is_util(r))
    fx["records"] = [r for r in fx["records"] if not is_util(r)] + [copy.deepcopy(first) for _ in range(3)]
    return fx


def test_one_reading_replayed_three_times_is_not_sustained():
    report = run(replayed()).report
    assert report.status not in ("diagnosed", "probable"), (report.status, report.confidence)
    assert any("duplicate" in l for l in report.limitations), report.limitations


def test_conflicting_readings_at_one_instant_are_unusable():
    fx = replayed()
    copies = [r for r in fx["records"] if is_util(r)]
    copies[1]["metrics"]["link_utilization_pct"] = 12.0
    report = run(fx).report
    assert report.status not in ("diagnosed", "probable")
    assert any("conflicting" in l for l in report.limitations), report.limitations


def test_samples_seconds_apart_are_not_sustained():
    fx = replayed()
    for i, r in enumerate(r for r in fx["records"] if is_util(r)):
        r["timestamp"] = f"2026-11-01T10:04:0{i}Z"
    report = run(fx).report
    assert report.status != "diagnosed", report.limitations


def test_recovery_inside_the_window_is_not_diagnosed():
    fx = copy.deepcopy(scenario("congestion"))
    fx.pop("on_query", None)
    samples = sorted((r for r in fx["records"] if is_util(r)), key=lambda r: r["timestamp"])
    samples[-1]["metrics"]["link_utilization_pct"] = 20.0
    samples[-1]["metrics"]["throughput_mbps"] = 200.0
    assert run(fx).report.status != "diagnosed"


# --------------------------------------------------------------------------- verification


def test_verification_that_contradicts_the_leader_blocks_diagnosed():
    fx = copy.deepcopy(scenario("congestion"))
    for r in fx["on_query"]:
        if "link_utilization_pct" in r["metrics"]:
            r["metrics"]["link_utilization_pct"] = 10.0
    report = run(fx).report
    assert report.status == "probable", (report.status, report.confidence)
    assert report.confidence <= 0.69
    assert any("did not confirm" in l for l in report.limitations), report.limitations


def test_verification_that_agrees_keeps_diagnosed():
    assert run(scenario("congestion")).report.status == "diagnosed"


# --------------------------------------------------------------------------- protocol boundaries


def test_non_mapping_extra_is_a_typed_error():
    fx = copy.deepcopy(scenario("congestion"))
    fx["messages"] = [valid_finding(fx, extra="invalid", surprise=1)]
    assert errors(run(fx), "INVALID_VALUE")


def test_unrepresentable_legacy_timestamp_is_a_typed_error():
    fx = copy.deepcopy(scenario("schema_0_9"))
    fx["messages"][0]["ts"] = 1e300
    assert errors(run(fx), "INVALID_VALUE")


def test_http_non_mapping_extra_is_a_typed_400():
    bundle = wire_bundle()
    bundle["messages"][0] |= {"extra": "invalid", "surprise": 1}
    reply = TestClient(create_app(":memory:"), raise_server_exceptions=False).post("/messages", json=bundle)
    assert reply.status_code == 400 and reply.json()["messages"][0]["payload"]["code"] == "INVALID_VALUE"


def test_bus_never_raises_on_mutated_messages():
    base = valid_finding(copy.deepcopy(scenario("congestion")))
    junk = [None, 5, -1.5, 1e300, True, "x", [], {}, [1], {"a": 1}]
    rng = random.Random(11)
    bus = MessageBus()
    for _ in range(3000):
        m = copy.deepcopy(base)
        m["message_id"] = f"M-{rng.random()}"
        for _ in range(rng.randint(1, 3)):
            where = m if rng.random() < 0.6 or not isinstance(m.get("payload"), dict) else m["payload"]
            where[rng.choice(list(where) + ["extra", "surprise", "ts", "kind", "body"])] = copy.deepcopy(rng.choice(junk))
        if rng.random() < 0.3:
            m["protocol"] = "netcoop/0.9"
        accepted, error = bus.deliver(m)
        assert (accepted is None) != (error is None)


def test_http_never_500s_on_mutated_messages():
    client = TestClient(create_app(":memory:"), raise_server_exceptions=False)
    junk = [None, 5, 1e300, True, "x", [], {}, [1], {"a": 1}]
    rng = random.Random(12)
    for _ in range(300):
        bundle = wire_bundle()
        m = bundle["messages"][rng.randrange(len(bundle["messages"]))]
        m[rng.choice(list(m) + ["extra", "surprise"])] = copy.deepcopy(rng.choice(junk))
        assert client.post("/messages", json=bundle).status_code != 500


# --------------------------------------------------------------------------- message context


def test_finding_from_another_incident_is_rejected():
    fx = copy.deepcopy(scenario("congestion"))
    fx["messages"] = [valid_finding(fx, incident_id="OTHER-INCIDENT")]
    result = run(fx)
    assert errors(result, "INVALID_VALUE")
    assert not [f for f in result.findings if f.message_id == "X-1"]


def test_finding_from_an_unregistered_sender_is_rejected():
    fx = copy.deepcopy(scenario("congestion"))
    fx["messages"] = [valid_finding(fx, sender="fake_agent")]
    result = run(fx)
    assert errors(result, "INVALID_VALUE")
    assert not [f for f in result.findings if f.sender == "fake_agent"]


def test_finding_outside_the_senders_capability_is_rejected():
    fx = copy.deepcopy(scenario("congestion"))
    fx["messages"] = [valid_finding(fx, sender="performance_agent")]      # device_overload is not on its card
    assert not [f for f in run(fx).findings if f.message_id == "X-1"]


def test_reply_to_a_message_that_was_never_sent_is_rejected():
    fx = copy.deepcopy(scenario("congestion"))
    fx["messages"] = [valid_finding(fx, in_reply_to="NO-SUCH-MESSAGE")]
    assert not [f for f in run(fx).findings if f.message_id == "X-1"]


def test_only_correlation_may_send_a_decision():
    fx = copy.deepcopy(scenario("congestion"))
    fx["messages"] = [valid_finding(fx, type="DECISION", payload={"decision_id": "D", "status": "healthy"})]
    assert errors(run(fx), "INVALID_VALUE")


def test_http_peer_from_another_incident_is_dropped():
    bundle = wire_bundle()
    bundle["messages"][1]["incident_id"] = "OTHER-INCIDENT"
    reply = TestClient(create_app(":memory:")).post("/messages", json=bundle).json()["messages"]
    assert [m for m in reply if m["type"] == "ERROR" and m["payload"]["code"] == "INVALID_VALUE"]
    assert not [m for m in reply if m["type"] == "FINDING" and m["payload"]["consumed_message_ids"]]


# --------------------------------------------------------------------------- Connectivity semantics


def test_error_only_connectivity_reply_counts_as_no_answer():
    fx = scenario("congestion")

    def transport(bundle):
        error = {"protocol": "netcoop/1.0", "message_id": "C-ERR", "incident_id": fx["incident_id"],
                 "in_reply_to": None, "sender": "connectivity_agent", "recipient": "orchestrator", "type": "ERROR",
                 "timestamp": bundle.messages[0].timestamp.isoformat(),
                 "payload": {"code": "AGENT_FAILURE", "detail": "analysis crashed"}}
        return ConnectivityReply(messages=[error], http_status=200, request_body={})

    components = default_components(connectivity="local")
    components.connectivity = transport
    report = run(fx, components).report
    assert report.status == "probable", report.status
    assert any("analysis crashed" in l for l in report.limitations), report.limitations


# --------------------------------------------------------------------------- healthy coverage


def only(fx, keep_metric) -> dict:
    out = copy.deepcopy(fx)
    kept = []
    for r in out["records"]:
        r["metrics"] = {k: v for k, v in r["metrics"].items() if keep_metric(k)}
        if r["metrics"]:
            kept.append(r)
    out["records"] = kept
    out.pop("on_query", None)
    return out


def test_one_dns_check_and_one_ping_are_not_healthy():
    fx = only(scenario("normal"), lambda k: k not in ("dns_success", "dhcp_success", "reachability"))
    base = scenario("normal")
    dns = next(r for r in base["records"] if "dns_success" in r["metrics"])
    ping = next(r for r in base["records"] if "reachability" in r["metrics"])
    fx["records"] += [{**dns, "metrics": {"dns_success": True}}, {**ping, "metrics": {"reachability": True}}]
    report = run(fx).report
    assert report.status == "insufficient_evidence", report.status
    assert any("expected" in l for l in report.limitations), report.limitations


def test_rssi_alone_is_not_infrastructure_coverage():
    drop = ("cpu_pct", "cpu_utilization_pct", "memory_pct", "memory_utilization_pct", "interface_errors",
            "crc_errors", "queue_drops")
    assert run(only(scenario("normal"), lambda k: k not in drop)).report.status == "insufficient_evidence"


def test_full_coverage_is_still_healthy():
    assert run(scenario("normal")).report.status == "healthy"


# --------------------------------------------------------------------------- configuration is executable


def configured(**changes) -> dict:
    signatures = copy.deepcopy(SIGNATURES)
    for path, value in changes.items():
        node = signatures
        *parents, leaf = path.split("__")
        for key in parents:
            node = node.setdefault(key, {})
        node[leaf] = value
    return signatures


def test_zero_weights_produce_a_zero_score():
    sig = configured(confidence__signal_fit_weight=0, confidence__topology_fit_weight=0,
                     confidence__time_fit_weight=0, confidence__independent_source_fit_weight=0)
    report = run(scenario("congestion"), signatures=sig).report
    assert report.confidence == 0.0 and report.status != "diagnosed"


def test_impossible_gate_prevents_diagnosed():
    sig = configured(decision_gates__diagnosed__min_confidence=1.01)
    assert run(scenario("congestion"), signatures=sig).report.status == "probable"


def test_lead_gate_is_read_from_configuration():
    sig = configured(decision_gates__diagnosed__min_lead_over_runner_up=0.0)
    assert run(scenario("ambiguous_congestion_errors"), signatures=sig).report.status == "diagnosed"


def test_query_rounds_are_read_from_configuration():
    sig = configured(verification__max_query_rounds=0)
    assert not [m for m in run(scenario("congestion"), signatures=sig).messages if m.type == "QUERY"]


def test_reported_cap_is_read_from_configuration():
    sig = configured(confidence__reported_caps__diagnosed=0.9)
    assert run(scenario("congestion"), signatures=sig).report.confidence == 0.9


# --------------------------------------------------------------------------- containment


def test_hung_correlation_ends_in_an_abstention(monkeypatch):
    monkeypatch.setattr(pipeline, "AGENT_DEADLINE_S", 0.2)
    monkeypatch.setattr(CorrelationAgent, "rank", lambda self, ctx, findings: time.sleep(3))
    started = time.monotonic()
    result = run(scenario("congestion"))
    assert time.monotonic() - started < 2.5
    assert result.report.status == "insufficient_evidence" and errors(result, "AGENT_TIMEOUT")


def test_crashing_transport_is_a_typed_failure():
    components = default_components(connectivity="local")

    def boom(bundle):
        raise RuntimeError("transport bug")

    components.connectivity = boom
    result = run(scenario("congestion"), components)
    assert result.report.status == "probable" and errors(result, "AGENT_FAILURE")


def test_timed_out_worker_cannot_touch_the_incident(monkeypatch):
    monkeypatch.setattr(pipeline, "AGENT_DEADLINE_S", 0.1)
    components = default_components(connectivity="local")
    done: list = []

    def late(query, ctx):
        time.sleep(0.3)
        ctx.observations.clear()
        done.append(True)

    components.agents["performance_agent"].answer = late
    fx = scenario("congestion")
    result = run(fx, components)
    time.sleep(0.5)
    assert done and result.report.supporting_evidence


# --------------------------------------------------------------------------- report and CLI


def test_impact_scope_is_separate_from_fault_location():
    report = run(scenario("congestion")).report
    assert report.affected_devices == ["SW_CAMPUS_1"]
    assert {"SW_CAMPUS_1", "SW_CAMPUS_3", "HOSTEL_AP_01"} <= set(report.impact_scope) and len(report.impact_scope) == 6
    Report.model_validate(report.model_dump(mode="json"))


def test_no_impact_scope_without_a_named_cause():
    assert run(scenario("normal")).report.impact_scope == []


def test_run_exit_code_reflects_the_outcome(tmp_path):
    args = ["--local", "--out", str(tmp_path / "r"), "--traces", str(tmp_path / "t")]
    assert main(["run", str(ROOT / "scenarios/dev/congestion.json"), *args]) == 0
    assert main(["run", str(ROOT / "scenarios/dev/malformed_input.json"), *args]) == 2
