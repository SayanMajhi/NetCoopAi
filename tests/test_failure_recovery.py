"""What a run does when a component misbehaves: every failure ends in a schema-valid report with a
typed ERROR in the trace, and a run that lost an agent never claims more than it checked."""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path

import netcoop.orchestrator.run as pipeline
from netcoop.agents.correlation import CorrelationAgent
from netcoop.agents.reporting import ReportingAgent
from netcoop.orchestrator.remote import send_bundle
from netcoop.orchestrator.run import default_components, load_signatures, load_topology, run_incident
from netcoop.orchestrator.transport import ConnectivityReply
from netcoop.protocol.models import Report

ROOT = Path(__file__).resolve().parents[1]
TOPOLOGY, SIGNATURES = load_topology(), load_signatures()


def scenario(name: str) -> dict:
    return json.loads((ROOT / "scenarios" / "dev" / f"{name}.json").read_text(encoding="utf-8"))


def run(fx, components=None):
    return run_incident(fx, TOPOLOGY, SIGNATURES, components=components or default_components(connectivity="local"))


def errors(result, code):
    return [m for m in result.messages if m.type == "ERROR" and m.payload["code"] == code]


def unreachable():
    components = default_components(connectivity="local")
    components.connectivity = lambda bundle: send_bundle(bundle, "http://127.0.0.1:9", timeout=0.2, retries=0)
    return components


def test_query_responder_crash_is_a_typed_error_and_the_decision_still_ships():
    components = default_components(connectivity="local")
    def broken(query, ctx):
        raise RuntimeError("probe driver crashed")
    components.agents["performance_agent"].answer = broken
    result = run(scenario("congestion"), components)
    Report.model_validate(result.report.model_dump(mode="json"))
    assert errors(result, "AGENT_FAILURE") and any("probe driver crashed" in l for l in result.report.limitations)
    assert result.report.confidence_method == "heuristic-v1"           # Correlation finished, no fallback
    assert not errors(result, "DUPLICATE_MESSAGE")


def test_hung_query_responder_cannot_stall_the_incident(monkeypatch):
    monkeypatch.setattr(pipeline, "AGENT_DEADLINE_S", 0.2)
    components = default_components(connectivity="local")
    def hang(query, ctx):
        time.sleep(3)
    components.agents["performance_agent"].answer = hang
    started = time.monotonic()
    result = run(scenario("congestion"), components)
    assert time.monotonic() - started < 2.5
    assert errors(result, "AGENT_TIMEOUT")


def test_correlation_retry_does_not_report_its_own_queries_as_duplicates(monkeypatch):
    real, calls = CorrelationAgent.decide, []
    def fails_once(self, ctx, in_reply_to, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("decision bug")
        return real(self, ctx, in_reply_to, **kw)
    monkeypatch.setattr(CorrelationAgent, "decide", fails_once)
    result = run(scenario("congestion"))
    assert len(calls) == 2
    assert not errors(result, "DUPLICATE_MESSAGE"), result.report.validation_errors
    assert result.report.status in ("diagnosed", "probable")


def test_reporting_crash_still_returns_a_failed_report(monkeypatch):
    def broken(self, *args, **kw):
        raise RuntimeError("template bug")
    monkeypatch.setattr(ReportingAgent, "build", broken)
    result = run(scenario("congestion"))
    Report.model_validate(result.report.model_dump(mode="json"))
    assert result.report.status == "failed" and result.report.suspected_root_cause is None
    assert any("template bug" in l for l in result.report.limitations)
    assert result.messages, "the trace survives a reporting failure"


def test_unreachable_connectivity_never_yields_diagnosed():
    report = run(scenario("congestion"), unreachable()).report
    assert report.status == "probable", (report.status, report.limitations)
    assert report.confidence <= 0.69
    assert any("blast radius" in l for l in report.limitations)


def test_unreachable_connectivity_never_yields_healthy():
    report = run(scenario("normal"), unreachable()).report
    assert report.status == "insufficient_evidence", (report.status, report.limitations)


def test_unreachable_limitation_names_the_error_code_once():
    report = run(scenario("congestion"), unreachable()).report
    line = next(l for l in report.limitations if "connectivity_unreachable" in l)
    assert line.count("AGENT_TIMEOUT") == 1, line


def test_control_run_is_not_treated_as_connectivity_down():
    result = run_incident(scenario("congestion"), TOPOLOGY, SIGNATURES, connectivity="local", control=True)
    assert result.report.status == "diagnosed"


def test_healthy_report_cites_no_stale_reading():
    fx = copy.deepcopy(scenario("normal"))
    fx["complaint"] = "Hostel block slow"      # scopes the healthy gate; the stale reading below is out of area
    target = next(r for r in fx["records"] if str(r.get("link_id") or r.get("device_id")).startswith("DEPT")
                  and {"latency_ms", "link_utilization_pct"} & set(r["metrics"]))
    stale = copy.deepcopy(target)
    stale["timestamp"] = "2026-11-01T09:00:00Z"
    stale["source_id"] = "old-poller"          # its own series, so it is the newest sample of that series
    fx["records"].append(stale)
    result = run(fx)
    assert result.report.status == "healthy", result.report.limitations
    assert not [e for e in result.report.supporting_evidence if e.source_id == "old-poller"]


def test_connectivity_reply_without_a_valid_message_counts_as_no_answer():
    components = default_components(connectivity="local")
    for junk in ([], ["not a message"]):
        components.connectivity = lambda bundle, junk=junk: ConnectivityReply(messages=junk, http_status=200)
        report = run(scenario("congestion"), components).report
        assert report.status == "probable", (report.status, report.limitations)
    assert run(scenario("normal"), components).report.status == "insufficient_evidence"


def test_non_list_messages_is_invalid_input():
    fx = copy.deepcopy(scenario("congestion"))
    fx["messages"] = 5
    assert run(fx).report.status == "invalid_input"


def test_agent_returning_garbage_is_a_typed_failure():
    components = default_components(connectivity="local")
    components.agents["performance_agent"].handle = lambda task, ctx: None
    result = run(scenario("congestion"), components)
    Report.model_validate(result.report.model_dump(mode="json"))
    assert errors(result, "AGENT_FAILURE")
