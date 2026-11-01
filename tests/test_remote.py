"""Remote Connectivity client (C4): Bundle over HTTP, timeout, one retry, never raises."""
from __future__ import annotations

import json
from typing import Any, Callable

import httpx
from fastapi.testclient import TestClient

from connectivity_service.app import create_app
from netcoop.agents.infrastructure import InfrastructureAgent
from netcoop.normalizer.result import NormalizationResult
from netcoop.orchestrator.remote import send_bundle
from netcoop.orchestrator.run import Components, run_incident
from netcoop.protocol.models import Bundle, Envelope, MessageType, Report
from tests.evidence_builders import CLOCK, TOPOLOGY, context, observations, signatures

ENDPOINT = "http://connectivity.test"
OBS = observations([("SW_CAMPUS_1", "queue_drops", [1200, 1600, 2100, 2700, 3300]),
                    ("HOSTEL_UPLINK_01", "link_utilization_pct", [92, 95, 96, 94, 97]),
                    ("SW_CAMPUS_3", "dns_success", [True] * 5)])


def bundle() -> Bundle:
    ctx = context(OBS)
    task = Envelope(protocol="netcoop/1.0", message_id="INC-0101-ORCH-03", incident_id="INC-0101", in_reply_to=None,
                    sender="orchestrator", recipient="connectivity_agent", type=MessageType.TASK, timestamp=ctx.clock,
                    payload={"task_id": "INC-0101-T-CONNECTIVITY", "capability": "analyze_connectivity",
                             "scope_ids": ["HOSTEL_UPLINK_01", "SW_CAMPUS_1", "SW_CAMPUS_3"]})
    return Bundle(messages=[task], observations=OBS, topology=TOPOLOGY)


def mock(*responses: Any) -> tuple[httpx.Client, list[httpx.Request]]:
    """A client whose transport plays back `responses` in order: an exception, or (status, body)."""
    seen: list[httpx.Request] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        status, body = item
        return httpx.Response(status, content=body if isinstance(body, bytes) else json.dumps(body).encode())

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


ERROR_BUNDLE = {"messages": [{"type": "ERROR", "payload": {"code": "MISSING_FIELD", "detail": "'sender' missing"}}]}


# --------------------------------------------------------------------------- against the real service


def test_real_service_round_trip_keeps_both_bodies() -> None:
    reply = send_bundle(bundle(), "http://testserver", client=TestClient(create_app(":memory:")))
    assert reply.error is None and reply.http_status == 200
    assert reply.request_body == bundle().model_dump(mode="json")
    assert reply.response_body == {"messages": reply.messages}
    supports = [m["payload"]["hypothesis"] for m in reply.messages if m["payload"]["stance"] == "supports"]
    assert supports == ["healthy@HOSTEL_UPLINK_01"]


def test_endpoint_trailing_slash_is_fine() -> None:
    client, seen = mock((200, {"messages": []}))
    send_bundle(bundle(), ENDPOINT + "/", client=client)
    assert str(seen[0].url) == ENDPOINT + "/messages"
    assert json.loads(seen[0].content) == bundle().model_dump(mode="json")


# --------------------------------------------------------------------------- unreachable / slow


def test_unreachable_agent_is_retried_once_then_agent_timeout() -> None:
    client, seen = mock(httpx.ConnectError("refused"), httpx.ConnectError("refused"))
    reply = send_bundle(bundle(), ENDPOINT, client=client)
    assert len(seen) == 2
    assert reply.error.startswith("AGENT_TIMEOUT: connectivity_unreachable: cannot reach")
    assert reply.messages == [] and reply.http_status is None and reply.response_body is None
    assert reply.request_body["messages"][0]["type"] == "TASK"      # the trace still shows what was sent


def test_timeout_is_retried_once_then_agent_timeout() -> None:
    client, seen = mock(httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow"))
    reply = send_bundle(bundle(), ENDPOINT, client=client, timeout=0.5)
    assert len(seen) == 2 and "within 0.5s" in reply.error and "connectivity_unreachable" in reply.error and reply.error.startswith("AGENT_TIMEOUT")


def test_retry_succeeds_on_second_attempt() -> None:
    client, seen = mock(httpx.ConnectError("refused"), (200, {"messages": [{"type": "FINDING"}]}))
    reply = send_bundle(bundle(), ENDPOINT, client=client)
    assert len(seen) == 2 and reply.error is None and reply.messages == [{"type": "FINDING"}]


def test_gateway_error_is_retried() -> None:
    client, seen = mock((503, b"busy"), (200, {"messages": []}))
    reply = send_bundle(bundle(), ENDPOINT, client=client)
    assert len(seen) == 2 and reply.error is None and reply.http_status == 200


def test_no_retry_when_disabled() -> None:
    client, seen = mock(httpx.ConnectError("refused"))
    assert send_bundle(bundle(), ENDPOINT, client=client, retries=0).error.startswith("AGENT_TIMEOUT")
    assert len(seen) == 1


def test_really_closed_port_never_raises() -> None:
    reply = send_bundle(bundle(), "http://127.0.0.1:9", timeout=0.5)
    assert reply.error.startswith("AGENT_TIMEOUT: connectivity_unreachable")


# --------------------------------------------------------------------------- bad replies


def test_typed_error_reply_is_not_retried_and_messages_go_to_the_bus() -> None:
    client, seen = mock((400, ERROR_BUNDLE))
    reply = send_bundle(bundle(), ENDPOINT, client=client)
    assert len(seen) == 1 and reply.http_status == 400
    assert reply.error == "AGENT_FAILURE: HTTP 400: MISSING_FIELD: 'sender' missing"
    assert reply.messages == ERROR_BUNDLE["messages"]


def test_server_error_is_not_retried() -> None:
    client, seen = mock((500, ERROR_BUNDLE))
    assert send_bundle(bundle(), ENDPOINT, client=client).error.startswith("AGENT_FAILURE: HTTP 500")
    assert len(seen) == 1


def test_non_json_reply_is_agent_failure_with_raw_text() -> None:
    client, _ = mock((200, b"<html>oops</html>"))
    reply = send_bundle(bundle(), ENDPOINT, client=client)
    assert reply.error == "AGENT_FAILURE: HTTP 200: reply is not JSON"
    assert reply.response_body == "<html>oops</html>" and reply.messages == []


def test_reply_that_is_not_a_bundle_is_agent_failure() -> None:
    for body in ([{"type": "FINDING"}], {"type": "RESPONSE"}, {"messages": "nope"}):
        client, _ = mock((200, body))
        reply = send_bundle(bundle(), ENDPOINT, client=client)
        assert "not a Bundle" in reply.error and reply.messages == [] and reply.response_body == body


def test_unexpected_client_error_never_raises() -> None:
    class Broken(httpx.Client):
        def post(self, *_: Any, **__: Any) -> httpx.Response:
            raise RuntimeError("boom")

    reply = send_bundle(bundle(), ENDPOINT, client=Broken())
    assert reply.error == "AGENT_FAILURE: RuntimeError: boom"


# --------------------------------------------------------------------------- in the pipeline


def _pipeline(send: Callable[[Bundle], Any], control: bool = False):
    def normalize(*_: Any) -> NormalizationResult:
        return NormalizationResult(observations=OBS, normalization_log=[], window_end=CLOCK)

    comps = Components(normalize=normalize, agents={"infrastructure_agent": InfrastructureAgent()}, connectivity=send)
    return run_incident({"incident_id": "INC-0101", "records": [{}]}, TOPOLOGY, signatures(),
                        components=comps, control=control)


def test_pipeline_over_http_traces_bodies_and_control_differs() -> None:
    service = TestClient(create_app(":memory:"))
    send = lambda b: send_bundle(b, "http://testserver", client=service)   # noqa: E731
    normal, control = _pipeline(send), _pipeline(send, control=True)
    for result in (normal, control):
        Report.model_validate(result.report.model_dump(mode="json"))
        [wire] = result.wire
        assert wire["http_status"] == 200 and wire["error"] is None
        assert wire["request"]["messages"][0]["type"] == "TASK" and wire["response"]["messages"]

    def conn(result) -> set[str]:
        return {f.payload["hypothesis"] for f in result.findings
                if f.sender == "connectivity_agent" and f.payload["stance"] == "supports"}

    assert "upstream_dependency@HOSTEL_UPLINK_01" in conn(normal)
    assert conn(control) == {"healthy@HOSTEL_UPLINK_01"}
    assert len(normal.wire[0]["request"]["messages"]) > 1 and len(control.wire[0]["request"]["messages"]) == 1


def test_pipeline_with_unreachable_agent_reports_connectivity_unreachable() -> None:
    result = _pipeline(lambda b: send_bundle(b, "http://127.0.0.1:9", timeout=0.5))
    Report.model_validate(result.report.model_dump(mode="json"))
    assert result.report.status == "insufficient_evidence"
    assert any("connectivity_unreachable" in lim for lim in result.report.limitations)
    errors = [m for m in result.messages if m.type == MessageType.ERROR.value and m.sender == "orchestrator"]
    assert errors and errors[0].payload["code"] == "AGENT_TIMEOUT"
    assert result.wire[0]["error"].startswith("AGENT_TIMEOUT") and result.wire[0]["request"]["messages"]
