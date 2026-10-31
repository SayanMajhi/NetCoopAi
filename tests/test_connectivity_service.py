"""Connectivity REST service: JSON Schema validation at the wire boundary."""
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
FINDING = json.loads((ROOT / "examples" / "finding.json").read_text(encoding="utf-8"))
QUERY = json.loads((ROOT / "examples" / "query.json").read_text(encoding="utf-8"))


def message(base: dict = FINDING, **overrides) -> dict:
    msg = copy.deepcopy(base)
    msg["recipient"] = "connectivity_agent"
    msg.update(overrides)
    return msg


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(":memory:"))


def assert_error(resp, status: int, code: str, field_path: str | None) -> dict:
    assert resp.status_code == status
    body = resp.json()
    SCHEMA.validate(body)
    assert body["type"] == "ERROR" and body["sender"] == "connectivity_agent"
    assert body["payload"]["code"] == code
    assert body["payload"]["field_path"] == field_path
    return body


def test_card_matches_registered_card(client):
    resp = client.get("/card")
    assert resp.status_code == 200
    assert resp.json() == CARD == CARDS["connectivity_agent"].model_dump()


def test_valid_finding_gets_response_with_in_reply_to(client):
    resp = client.post("/messages", json=message())
    assert resp.status_code == 200
    body = resp.json()
    SCHEMA.validate(body)
    assert body["type"] == "RESPONSE"
    assert body["in_reply_to"] == "INC-0001-INF-02"
    assert body["recipient"] == "infrastructure_agent"
    assert body["message_id"] == "INC-0001-CONN-01"


def test_query_response_echoes_query_id(client):
    body = client.post("/messages", json=message(QUERY)).json()
    assert body["type"] == "RESPONSE" and body["payload"]["query_id"] == "Q-01"


def test_missing_sender_is_typed_error(client):
    msg = message()
    del msg["sender"]
    body = assert_error(client.post("/messages", json=msg), 400, "MISSING_FIELD", "sender")
    assert body["in_reply_to"] == "INC-0001-INF-02"
    assert body["payload"]["offending_message_id"] == "INC-0001-INF-02"


def test_missing_payload_field_reports_path(client):
    msg = message()
    del msg["payload"]["stance"]
    assert_error(client.post("/messages", json=msg), 400, "MISSING_FIELD", "payload.stance")


def test_bad_enum_value_is_invalid_value(client):
    msg = message()
    msg["payload"]["stance"] = "maybe"
    assert_error(client.post("/messages", json=msg), 400, "INVALID_VALUE", "payload.stance")


def test_v2_is_unsupported(client):
    assert_error(client.post("/messages", json=message(protocol="netcoop/2.0")),
                 400, "SCHEMA_VERSION_UNSUPPORTED", "protocol")


def test_malformed_json_is_typed_error(client):
    body = assert_error(client.post("/messages", content=b"{not json"), 400, "MALFORMED_JSON", None)
    assert body["incident_id"] == "UNKNOWN"


def test_duplicate_is_ignored_and_logged(client):
    assert client.post("/messages", json=message()).status_code == 200
    assert_error(client.post("/messages", json=message()), 409, "DUPLICATE_MESSAGE", "message_id")
    rows = client.get("/events").json()
    assert [r["valid"] for r in rows] == [1, 0]
    assert rows[1]["error"].startswith("DUPLICATE_MESSAGE")


def test_unknown_1x_field_is_accepted_into_extra(client):
    resp = client.post("/messages", json=message(protocol="netcoop/1.2", priority="high"))
    assert resp.status_code == 200


def test_acceptance_three_rows_logged(tmp_path):
    db = tmp_path / "conn.sqlite"
    client = TestClient(create_app(db))
    client.post("/messages", json=message())
    bad = message(message_id="INC-0001-INF-03")
    del bad["sender"]
    client.post("/messages", json=bad)
    client.post("/messages", json=message())
    rows = client.get("/events").json()
    assert [(r["message_id"], r["valid"]) for r in rows] == [
        ("INC-0001-INF-02", 1), ("INC-0001-INF-03", 0), ("INC-0001-INF-02", 0),
    ]
    assert db.exists()
