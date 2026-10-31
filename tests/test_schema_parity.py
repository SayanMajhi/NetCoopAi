"""The exported JSON Schemas must reject everything the Python models reject.

Non-Python consumers (the Connectivity REST process, the dashboard) only see
schemas/*.json, so each bad input here must fail in all three places: Pydantic
(the Python bus), jsonschema with format checking, and the REST service.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from connectivity_service.app import create_app
from netcoop.orchestrator.bus import MessageBus
from netcoop.protocol.models import Envelope, Observation, Report

ROOT = Path(__file__).resolve().parents[1]


def _schema(name: str) -> Draft202012Validator:
    schema = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


MESSAGE_SCHEMA = _schema("message-1.0.json")
REPORT_SCHEMA = _schema("report-1.0.json")
OBSERVATION_SCHEMA = _schema("observation-1.0.json")


def load(name: str) -> dict[str, Any]:
    return json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))


def changed(base: str, change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    body = copy.deepcopy(load(base))
    change(body)
    return body


def _set(path: str, value: Any) -> Callable[[dict[str, Any]], None]:
    def apply(body: dict[str, Any]) -> None:
        *parents, last = path.split(".")
        for key in parents:
            body = body[key]
        body[last] = value
    return apply


def _drop(path: str) -> Callable[[dict[str, Any]], None]:
    def apply(body: dict[str, Any]) -> None:
        *parents, last = path.split(".")
        for key in parents:
            body = body[key]
        del body[last]
    return apply


BAD_MESSAGES = {
    "unknown cause in hypothesis": changed("finding.json", _set("payload.hypothesis", "bogus@X")),
    "unknown cause in target_hypothesis": changed("finding.json", _set("payload.target_hypothesis", "bogus@X")),
    "timestamp not a date": changed("finding.json", _set("timestamp", "yesterday")),
    "timestamp without timezone": changed("finding.json", _set("timestamp", "2026-10-31T10:30:00")),
    "timestamp as unix number": changed("finding.json", _set("timestamp", 1793356200)),
    "missing stance": changed("finding.json", _drop("payload.stance")),
    "empty evidence_ids": changed("finding.json", _set("payload.evidence_ids", [])),
    "score as string": changed("finding.json", _set("payload.score", "0.6")),
    "payload does not match type": changed("finding.json", _set("type", "QUERY")),
    "ranking with unknown cause": changed("decision.json", lambda b: b["payload"]["ranking"][0].update(hypothesis="bogus@X")),
    "ranking confidence as string": changed("decision.json", lambda b: b["payload"]["ranking"][0].update(confidence="0.8")),
}

BAD_REPORTS = {
    "insufficient_evidence names a cause": changed("report_insufficient.json", _set("suspected_root_cause", "link_congestion@X")),
    "diagnosed without a cause": changed("report_diagnosed.json", _set("suspected_root_cause", None)),
    "diagnosed without confidence": changed("report_diagnosed.json", _set("confidence", None)),
    "invalid_input with confidence": changed("report_invalid_input.json", _set("confidence", 0.5)),
    "invalid_input without validation_errors": changed("report_invalid_input.json", _set("validation_errors", [])),
    "confidence as string": changed("report_diagnosed.json", _set("confidence", "0.81")),
}

GOOD_OBSERVATION = {
    "evidence_id": "EV-001", "source_id": "organizer-generator", "observed_at": "2026-10-31T17:22:28Z",
    "device_id": "SW_CAMPUS_2", "metric": "cpu_pct", "value": 11.13, "unit": "percent",
    "original_field": "metrics.cpu_utilization_pct", "original_value": 11.13,
}

BAD_OBSERVATIONS = {
    "no device_id or link_id": {k: v for k, v in GOOD_OBSERVATION.items() if k != "device_id"},
    "device_id null": {**GOOD_OBSERVATION, "device_id": None},
    "observed_at without timezone": {**GOOD_OBSERVATION, "observed_at": "2026-10-31T17:22:28"},
}


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app(":memory:"))


def post_as_peer(client: TestClient, body: dict[str, Any]) -> list[dict[str, Any]]:
    """The REST service takes a Bundle: send `body` as a peer message behind a valid TASK."""
    task = load("task.json")
    resp = client.post("/messages", content=json.dumps({"messages": [task, body]}))
    assert resp.status_code == 200, f"REST returned {resp.status_code}"
    return resp.json()["messages"]


@pytest.mark.parametrize("name", sorted(BAD_MESSAGES))
def test_bad_message_rejected_everywhere(name: str, client: TestClient) -> None:
    body = BAD_MESSAGES[name]
    with pytest.raises(ValidationError):
        Envelope.model_validate(copy.deepcopy(body))
    assert list(MESSAGE_SCHEMA.iter_errors(body)), f"JSON Schema accepted: {name}"
    accepted, error = MessageBus().deliver(copy.deepcopy(body))
    assert accepted is None and error is not None, f"bus accepted: {name}"
    replies = post_as_peer(client, {**body, "message_id": f"PARITY-{name}"})
    assert [m["type"] for m in replies] == ["RESPONSE", "ERROR"], f"REST accepted: {name}"
    assert replies[1]["payload"]["offending_message_id"] == f"PARITY-{name}"


@pytest.mark.parametrize("name", sorted(BAD_REPORTS))
def test_bad_report_rejected_everywhere(name: str) -> None:
    body = BAD_REPORTS[name]
    with pytest.raises(ValidationError):
        Report.model_validate(body)
    assert list(REPORT_SCHEMA.iter_errors(body)), f"JSON Schema accepted: {name}"


@pytest.mark.parametrize("name", sorted(BAD_OBSERVATIONS))
def test_bad_observation_rejected_everywhere(name: str) -> None:
    body = BAD_OBSERVATIONS[name]
    with pytest.raises(ValidationError):
        Observation.model_validate(body)
    assert list(OBSERVATION_SCHEMA.iter_errors(body)), f"JSON Schema accepted: {name}"


@pytest.mark.parametrize("name", ["task.json", "finding.json", "query.json", "response.json", "decision.json", "error.json"])
def test_good_message_accepted_everywhere(name: str, client: TestClient) -> None:
    body = load(name)
    Envelope.model_validate(copy.deepcopy(body))
    assert not list(MESSAGE_SCHEMA.iter_errors(body))
    replies = post_as_peer(client, {**body, "message_id": f"PARITY-OK-{name}"})
    assert [m["type"] for m in replies] == ["RESPONSE"], f"REST rejected: {name}"


@pytest.mark.parametrize("name", ["report_diagnosed.json", "report_insufficient.json", "report_invalid_input.json"])
def test_good_report_accepted_everywhere(name: str) -> None:
    body = load(name)
    Report.model_validate(body)
    assert not list(REPORT_SCHEMA.iter_errors(body))


def test_good_observation_accepted_everywhere() -> None:
    Observation.model_validate(GOOD_OBSERVATION)
    assert not list(OBSERVATION_SCHEMA.iter_errors(GOOD_OBSERVATION))
