import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from netcoop.protocol.cards import CARDS
from netcoop.protocol.compat import UnsupportedVersion, upgrade
from netcoop.protocol.models import Envelope, Report

EX = Path(__file__).resolve().parents[1] / "examples"
VALID_MESSAGES = ["task.json", "finding.json", "query.json", "response.json", "decision.json", "error.json"]
REPORTS = ["report_diagnosed.json", "report_insufficient.json", "report_invalid_input.json"]


def load(name):
    return json.loads((EX / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", VALID_MESSAGES)
def test_example_messages_validate(name):
    Envelope.model_validate(load(name))


@pytest.mark.parametrize("name", REPORTS)
def test_example_reports_validate(name):
    Report.model_validate(load(name))


def test_finding_without_stance_names_the_field():
    msg = load("finding.json")
    del msg["payload"]["stance"]
    with pytest.raises(ValidationError) as exc:
        Envelope.model_validate(msg)
    assert "stance" in str(exc.value)


def test_unknown_cause_rejected():
    msg = load("finding.json")
    msg["payload"]["hypothesis"] = "aliens@HOSTEL_UPLINK_01"
    with pytest.raises(ValidationError):
        Envelope.model_validate(msg)


def test_finding_needs_evidence():
    msg = load("finding.json")
    msg["payload"]["evidence_ids"] = []
    with pytest.raises(ValidationError):
        Envelope.model_validate(msg)


def test_unknown_envelope_field_rejected():
    msg = load("finding.json")
    msg["colour"] = "red"
    with pytest.raises(ValidationError):
        Envelope.model_validate(msg)


def test_payload_must_match_type():
    msg = load("finding.json")
    msg["type"] = "QUERY"
    with pytest.raises(ValidationError):
        Envelope.model_validate(msg)


def test_v0_9_fails_plain_1_0_validation():
    with pytest.raises(ValidationError):
        Envelope.model_validate(load("finding_v0_9.json"))


def test_report_status_rules():
    r = load("report_insufficient.json")
    r["suspected_root_cause"] = "link_congestion@X"
    with pytest.raises(ValidationError):
        Report.model_validate(r)
    r = load("report_invalid_input.json")
    r["confidence"] = 0.5
    with pytest.raises(ValidationError):
        Report.model_validate(r)
    r = load("report_invalid_input.json")
    r["validation_errors"] = []
    with pytest.raises(ValidationError):
        Report.model_validate(r)


def test_v2_rejected():
    with pytest.raises(UnsupportedVersion):
        upgrade(load("message_v2_0.json"))


def test_v0_9_upgrades_with_a_visible_record():
    body, records = upgrade(load("finding_v0_9.json"))
    env = Envelope.model_validate(body)
    assert env.type == "FINDING" and env.sender == "infrastructure_agent"
    assert env.payload["hypothesis"] == "interface_fault@HOSTEL_UPLINK_01" and env.payload["stance"] == "supports"
    assert env.timestamp.isoformat() == "2026-10-30T10:30:00+00:00"
    assert records[0] == "upgraded netcoop/0.9 to netcoop/1.0" and "0.9 field 'msg_id' -> 'message_id'" in records


def test_v0_9_kinds_without_a_mapping_are_refused():
    msg = load("finding_v0_9.json")
    msg["kind"] = "alarm"
    with pytest.raises(UnsupportedVersion, match="alarm"):
        upgrade(msg)


def test_unknown_1_x_fields_preserved_in_extra():
    msg = load("finding.json")
    msg["protocol"] = "netcoop/1.1"
    msg["priority"] = "high"
    body, records = upgrade(msg)
    env = Envelope.model_validate(body)
    assert env.extra["priority"] == "high" and records


def test_agent_cards():
    assert len(CARDS) == 5
    for card in CARDS.values():
        assert "netcoop/1.0" in card.protocol_versions
