"""Connectivity REST service: Bundle in, Bundle out, JSON Schema validation at the wire boundary."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from connectivity_service.app import CARD, create_app
from netcoop.protocol.cards import CARDS

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = Draft202012Validator(json.loads((ROOT / "schemas" / "message-1.0.json").read_text(encoding="utf-8")))
BUNDLE_SCHEMA = Draft202012Validator(json.loads((ROOT / "schemas" / "bundle-1.0.json").read_text(encoding="utf-8")))
TASK = json.loads((ROOT / "examples" / "task.json").read_text(encoding="utf-8"))
FINDING = json.loads((ROOT / "examples" / "finding.json").read_text(encoding="utf-8"))
TOPOLOGY = json.loads((ROOT / "scenarios" / "topology.json").read_text(encoding="utf-8"))
OBSERVATION = {
    "evidence_id": "EV-014", "source_id": "switch-telemetry", "observed_at": "2026-10-31T10:29:00Z",
    "link_id": "HOSTEL_UPLINK_01", "metric": "link_utilization_pct", "value": 41.0, "unit": "%",
    "original_field": "metrics.link_utilization_pct", "original_value": 41.0,
    "normalization_steps": [], "quality_flags": ["ok"],
}


def message(base: dict, **overrides) -> dict:
    msg = copy.deepcopy(base)
    msg.update(overrides)
    return msg


def bundle(*messages: dict, **overrides) -> dict:
    body = {"messages": list(messages) or [message(TASK), message(FINDING)],
            "observations": [copy.deepcopy(OBSERVATION)], "topology": TOPOLOGY}
    body.update(overrides)
    return body


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(":memory:"))


def reply_messages(resp, status: int) -> list[dict]:
    assert resp.status_code == status
    body = resp.json()
    BUNDLE_SCHEMA.validate(body)              # the reply is a Bundle too
    for m in body["messages"]:
        SCHEMA.validate(m)
        assert m["sender"] == "connectivity_agent"
    return body["messages"]


def assert_error(resp, status: int, code: str, field_path: str | None) -> dict:
    errors = [m for m in reply_messages(resp, status) if m["type"] == "ERROR"]
    assert len(errors) == 1
    assert errors[0]["payload"]["code"] == code
    assert errors[0]["payload"]["field_path"] == field_path
    return errors[0]


def test_card_matches_registered_card(client):
    resp = client.get("/card")
    assert resp.status_code == 200
    assert resp.json() == CARD == CARDS["connectivity_agent"].model_dump()


def test_valid_bundle_gets_reply_bundle_answering_the_task(client):
    [reply] = reply_messages(client.post("/messages", json=bundle()), 200)
    assert reply["type"] == "RESPONSE"
    assert reply["in_reply_to"] == "INC-0001-ORCH-03" and reply["recipient"] == "orchestrator"
    assert reply["message_id"] == "INC-0001-CONN-01"
    assert reply["timestamp"] == TASK["timestamp"]           # incident clock, not wall clock
    assert "1 peer message" in reply["payload"]["answer"]


def test_bundle_with_only_a_task_is_valid(client):
    [reply] = reply_messages(client.post("/messages", json=bundle(message(TASK), observations=[])), 200)
    assert "0 peer message" in reply["payload"]["answer"]


def test_same_bundle_twice_gets_the_same_reply(client):
    """IDs repeat across runs by design (deterministic per fixture); each Bundle is one delivery."""
    first = client.post("/messages", json=bundle()).json()
    second = client.post("/messages", json=bundle()).json()
    assert first == second


def test_bare_envelope_is_not_a_bundle(client):
    assert_error(client.post("/messages", json=message(TASK)), 400, "MISSING_FIELD", "messages")


def test_empty_messages_is_invalid(client):
    assert_error(client.post("/messages", json=bundle(messages=[])), 400, "INVALID_VALUE", "messages")


def test_malformed_json_is_typed_error(client):
    error = assert_error(client.post("/messages", content=b"{not json"), 400, "MALFORMED_JSON", None)
    assert error["incident_id"] == "UNKNOWN"


def test_bad_observation_timestamp_fails_format_check(client):
    bad = dict(OBSERVATION, observed_at="yesterday")
    error = assert_error(client.post("/messages", json=bundle(observations=[bad])),
                         400, "INVALID_VALUE", "observations.0.observed_at")
    assert error["recipient"] == "orchestrator" and error["incident_id"] == "INC-0001"


def test_observation_without_target_is_rejected(client):
    bad = {k: v for k, v in OBSERVATION.items() if k != "link_id"}
    resp = client.post("/messages", json=bundle(observations=[bad]))
    assert resp.status_code == 400


def test_unknown_top_level_field_is_rejected(client):
    assert_error(client.post("/messages", json=bundle(priority="high")), 400, "INVALID_VALUE", None)


def test_missing_sender_on_task_is_typed_error(client):
    task = message(TASK)
    del task["sender"]
    error = assert_error(client.post("/messages", json=bundle(task, message(FINDING))),
                         400, "MISSING_FIELD", "messages.0.sender")
    assert error["in_reply_to"] == "INC-0001-ORCH-03"
    assert error["payload"]["offending_message_id"] == "INC-0001-ORCH-03"


def test_first_message_must_be_a_task(client):
    assert_error(client.post("/messages", json=bundle(message(FINDING))), 400, "INVALID_VALUE", "messages.0.type")


def test_task_for_another_agent_is_unknown_recipient(client):
    task = message(TASK, recipient="performance_agent")
    assert_error(client.post("/messages", json=bundle(task)), 400, "UNKNOWN_RECIPIENT", "messages.0.recipient")


def test_bad_timestamp_on_task_fails_format_check(client):
    task = message(TASK, timestamp="not-a-time")
    assert_error(client.post("/messages", json=bundle(task)), 400, "INVALID_VALUE", "messages.0.timestamp")


def test_v2_task_is_unsupported(client):
    task = message(TASK, protocol="netcoop/2.0")
    assert_error(client.post("/messages", json=bundle(task)), 400, "SCHEMA_VERSION_UNSUPPORTED", "messages.0.protocol")


def test_bad_peer_finding_is_dropped_with_error_and_task_still_answered(client):
    peer = message(FINDING)
    del peer["payload"]["stance"]
    messages = reply_messages(client.post("/messages", json=bundle(message(TASK), peer)), 200)
    assert [m["type"] for m in messages] == ["RESPONSE", "ERROR"]
    assert "0 peer message" in messages[0]["payload"]["answer"]
    assert messages[1]["payload"]["code"] == "MISSING_FIELD"
    assert messages[1]["payload"]["field_path"] == "messages.1.payload.stance"
    assert messages[1]["recipient"] == "infrastructure_agent"


def test_bad_enum_on_peer_is_invalid_value(client):
    peer = message(FINDING)
    peer["payload"]["stance"] = "maybe"
    assert_error(client.post("/messages", json=bundle(message(TASK), peer)), 200, "INVALID_VALUE",
                 "messages.1.payload.stance")


def test_v2_peer_is_unsupported_but_task_answered(client):
    peer = message(FINDING, protocol="netcoop/2.0")
    assert_error(client.post("/messages", json=bundle(message(TASK), peer)), 200, "SCHEMA_VERSION_UNSUPPORTED",
                 "messages.1.protocol")


def test_duplicate_in_bundle_is_ignored_and_logged(client):
    messages = reply_messages(client.post("/messages", json=bundle(message(TASK), message(FINDING),
                                                                     message(FINDING))), 200)
    assert "1 peer message" in messages[0]["payload"]["answer"]
    assert messages[1]["payload"]["code"] == "DUPLICATE_MESSAGE"
    rows = client.get("/events").json()
    assert [r["valid"] for r in rows] == [1, 1, 0]
    assert rows[2]["error"].startswith("DUPLICATE_MESSAGE")


def test_unknown_1x_field_is_accepted_into_extra(client):
    peer = message(FINDING, protocol="netcoop/1.2", priority="high")
    [reply] = reply_messages(client.post("/messages", json=bundle(message(TASK), peer)), 200)
    assert "1 peer message" in reply["payload"]["answer"]


def test_every_message_is_logged(tmp_path):
    db = tmp_path / "conn.sqlite"
    client = TestClient(create_app(db))
    bad = message(FINDING, message_id="INC-0001-INF-03")
    del bad["sender"]
    client.post("/messages", json=bundle(message(TASK), message(FINDING), bad, message(FINDING)))
    rows = client.get("/events").json()
    assert [(r["message_id"], r["valid"]) for r in rows] == [
        ("INC-0001-ORCH-03", 1), ("INC-0001-INF-02", 1), ("INC-0001-INF-03", 0), ("INC-0001-INF-02", 0),
    ]
    assert db.exists()
