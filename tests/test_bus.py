"""MessageBus + EventLog: every delivery is validated and logged; failures become typed ERRORs."""
from __future__ import annotations

import json

import pytest

from netcoop.orchestrator.bus import MessageBus
from netcoop.orchestrator.eventlog import EventLog
from netcoop.protocol.models import Envelope


def finding(**overrides) -> dict:
    msg = {
        "protocol": "netcoop/1.0",
        "message_id": "INC-0001-INF-02",
        "incident_id": "INC-0001",
        "in_reply_to": "INC-0001-ORCH-01",
        "sender": "infrastructure_agent",
        "recipient": "correlation_agent",
        "type": "FINDING",
        "timestamp": "2026-10-31T10:30:00Z",
        "payload": {
            "finding_id": "F-INF-01",
            "hypothesis": "link_congestion@HOSTEL_UPLINK_01",
            "stance": "contradicts",
            "target_hypothesis": "link_congestion@HOSTEL_UPLINK_01",
            "evidence_ids": ["EV-014"],
            "reason": "Interface errors rise while utilization stays below baseline",
        },
    }
    msg.update(overrides)
    return msg


@pytest.fixture
def bus() -> MessageBus:
    return MessageBus(EventLog(":memory:"))


def error_payload(err: Envelope) -> dict:
    assert err.type == "ERROR"
    return err.payload


def test_valid_message_is_accepted_and_logged(bus):
    env, err = bus.deliver(finding())
    assert err is None
    assert isinstance(env, Envelope) and env.message_id == "INC-0001-INF-02"
    [row] = bus.log.rows()
    assert row["valid"] == 1 and row["error"] is None
    assert row["sender"] == "infrastructure_agent" and row["type"] == "FINDING"
    assert row["protocol"] == "netcoop/1.0" and row["in_reply_to"] == "INC-0001-ORCH-01"


def test_json_string_input_is_accepted(bus):
    env, err = bus.deliver(json.dumps(finding()))
    assert err is None and env is not None


def test_duplicate_message_id_is_ignored_and_logged(bus):
    bus.deliver(finding())
    env, err = bus.deliver(finding())
    assert env is None
    assert error_payload(err)["code"] == "DUPLICATE_MESSAGE"
    rows = bus.log.rows()
    assert [r["valid"] for r in rows] == [1, 0]
    assert rows[1]["error"].startswith("DUPLICATE_MESSAGE")


def test_missing_field_becomes_typed_error(bus):
    msg = finding()
    del msg["sender"]
    env, err = bus.deliver(msg)
    assert env is None
    p = error_payload(err)
    assert p["code"] == "MISSING_FIELD" and p["field_path"] == "sender"
    assert p["offending_message_id"] == "INC-0001-INF-02"
    assert err.in_reply_to == "INC-0001-INF-02" and err.incident_id == "INC-0001"
    [row] = bus.log.rows()
    assert row["valid"] == 0 and row["error"].startswith("MISSING_FIELD")


def test_invalid_payload_reports_payload_field_path(bus):
    msg = finding()
    msg["payload"]["stance"] = "maybe"
    _, err = bus.deliver(msg)
    p = error_payload(err)
    assert p["code"] == "INVALID_VALUE" and p["field_path"] == "payload.stance"


def test_missing_payload_field_reports_payload_field_path(bus):
    msg = finding()
    del msg["payload"]["evidence_ids"]
    _, err = bus.deliver(msg)
    p = error_payload(err)
    assert p["code"] == "MISSING_FIELD" and p["field_path"] == "payload.evidence_ids"


def test_v2_is_unsupported(bus):
    env, err = bus.deliver(finding(protocol="netcoop/2.0"))
    assert env is None
    p = error_payload(err)
    assert p["code"] == "SCHEMA_VERSION_UNSUPPORTED" and p["field_path"] == "protocol"
    assert bus.log.rows()[0]["valid"] == 0


def test_malformed_json_is_logged_with_raw_text(bus):
    env, err = bus.deliver("{not json")
    assert env is None
    assert error_payload(err)["code"] == "MALFORMED_JSON"
    assert err.incident_id == "UNKNOWN"
    [row] = bus.log.rows()
    assert row["valid"] == 0 and row["body_json"] == "{not json"


def test_error_envelopes_are_themselves_valid_and_numbered(bus):
    _, err1 = bus.deliver(finding(protocol="netcoop/2.0"))
    _, err2 = bus.deliver(finding(protocol="netcoop/2.0"))
    for err in (err1, err2):
        Envelope.model_validate(err.model_dump(mode="json"))
        assert err.sender == "orchestrator" and err.recipient == "infrastructure_agent"
    assert err1.message_id == "INC-0001-BUS-ERR-01"
    assert err2.message_id == "INC-0001-BUS-ERR-02"


def test_rejected_message_id_can_be_resent_after_fix(bus):
    bad = finding()
    del bad["sender"]
    bus.deliver(bad)
    env, err = bus.deliver(finding())
    assert err is None and env is not None


def test_missing_protocol_is_missing_field(bus):
    msg = finding()
    del msg["protocol"]
    _, err = bus.deliver(msg)
    p = error_payload(err)
    assert p["code"] == "MISSING_FIELD" and p["field_path"] == "protocol"


def test_newer_1x_minor_is_accepted_with_unknown_fields_preserved(bus):
    env, err = bus.deliver(finding(protocol="netcoop/1.3", priority="high"))
    assert err is None
    assert env.protocol == "netcoop/1.0" and env.extra == {"priority": "high"}
    assert any("1.3" in r for r in bus.conversions["INC-0001-INF-02"])
    assert bus.log.rows()[0]["protocol"] == "netcoop/1.3"


def test_file_backed_log_persists(tmp_path):
    db = tmp_path / "events.sqlite"
    bus = MessageBus(EventLog(db))
    bus.deliver(finding())
    bus.deliver(finding())
    bus.log.close()
    rows = EventLog(db).rows(incident_id="INC-0001")
    assert [r["valid"] for r in rows] == [1, 0]
