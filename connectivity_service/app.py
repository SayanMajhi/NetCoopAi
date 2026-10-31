"""Connectivity Agent as a separate REST process.

It validates incoming bodies with jsonschema against schemas/message-1.0.json,
not the Python models, so the wire contract is what is actually enforced here.

    uvicorn connectivity_service.app:app --port 8001

Set NETCOOP_CONNECTIVITY_DB to a file path to keep the event log on disk
(default: in memory). GET /events returns the logged rows.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError, best_match

from netcoop.orchestrator.eventlog import EventLog

AGENT_ID = "connectivity_agent"
PROTOCOL = "netcoop/1.0"
SUPPORTED_MAJOR = 1
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "message-1.0.json"
_VERSION_RE = re.compile(r"^netcoop/(\d+)\.(\d+)$")

CARD: dict[str, Any] = {
    "agent_id": AGENT_ID,
    "endpoint": "http://127.0.0.1:8001",
    "protocol_versions": [PROTOCOL],
    "capabilities": ["analyze_connectivity", "interpret_peer_findings"],
    "owned_metrics": ["dns_success", "dhcp_success", "reachability"],
    "hypotheses": ["dns_failure", "dhcp_failure", "upstream_dependency", "healthy"],
}


class _Rejected(Exception):
    def __init__(self, code: str, detail: str, field_path: Optional[str], status: int) -> None:
        super().__init__(detail)
        self.code, self.detail, self.field_path, self.status = code, detail, field_path, status


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _str_or(value: Any, default: Optional[str]) -> Optional[str]:
    return value if isinstance(value, str) and value else default


def _field_path(error: ValidationError) -> str:
    parts = [str(p) for p in error.absolute_path]
    if error.validator == "required" and isinstance(error.instance, dict):
        missing = [k for k in error.validator_value if k not in error.instance]
        parts += missing[:1]
    return ".".join(parts)


class ConnectivityService:
    def __init__(self, log: EventLog) -> None:
        self.log = log
        self._validator = Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")),
                                               format_checker=Draft202012Validator.FORMAT_CHECKER)
        self._envelope_fields = set(self._validator.schema["properties"])
        self._seen: set[str] = set()
        self._reply_count = 0
        self._error_count = 0

    def handle(self, raw_text: str) -> tuple[int, dict[str, Any]]:
        body: Any = None
        try:
            body = self._parse(raw_text)
            message = self._check(body)
            if message["message_id"] in self._seen:
                raise _Rejected("DUPLICATE_MESSAGE",
                                f"message_id {message['message_id']} already received; ignored",
                                "message_id", 409)
        except _Rejected as rej:
            self.log.record(body, valid=False, error=f"{rej.code}: {rej.detail}", body_json=raw_text)
            return rej.status, self._error(body, rej)

        self._seen.add(message["message_id"])
        self.log.record(body, valid=True, body_json=raw_text)
        return 200, self._reply(message)

    @staticmethod
    def _parse(raw_text: str) -> dict[str, Any]:
        try:
            body = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise _Rejected("MALFORMED_JSON", f"body is not valid JSON: {exc}", None, 400) from exc
        if not isinstance(body, dict):
            raise _Rejected("MALFORMED_JSON", "body must be a JSON object", None, 400)
        return body

    def _check(self, body: dict[str, Any]) -> dict[str, Any]:
        """Version gate, move unknown 1.x fields into `extra`, then JSON Schema validation."""
        protocol = body.get("protocol")
        match = _VERSION_RE.match(protocol) if isinstance(protocol, str) else None
        if match and int(match.group(1)) != SUPPORTED_MAJOR:
            raise _Rejected("SCHEMA_VERSION_UNSUPPORTED",
                            f"{protocol} not supported; accepts netcoop/{SUPPORTED_MAJOR}.x",
                            "protocol", 400)
        message = {k: v for k, v in body.items() if k in self._envelope_fields}
        unknown = {k: v for k, v in body.items() if k not in self._envelope_fields}
        if unknown:
            message["extra"] = {**(body.get("extra") or {}), **unknown}
        error = best_match(self._validator.iter_errors(message))
        if error is not None:
            code = "MISSING_FIELD" if error.validator == "required" else "INVALID_VALUE"
            raise _Rejected(code, error.message, _field_path(error) or None, 400)
        return message

    def _reply(self, message: dict[str, Any]) -> dict[str, Any]:
        """Stub RESPONSE until the real connectivity analysis lands (Part 2)."""
        self._reply_count += 1
        payload = message["payload"]
        reply = {
            "protocol": PROTOCOL,
            "message_id": f"{message['incident_id']}-CONN-{self._reply_count:02d}",
            "incident_id": message["incident_id"],
            "in_reply_to": message["message_id"],
            "sender": AGENT_ID,
            "recipient": message["sender"],
            "type": "RESPONSE",
            "timestamp": _now(),
            "payload": {
                "query_id": payload.get("query_id") if message["type"] == "QUERY" else None,
                "evidence_ids": [],
                "answer": f"received {message['type']} {message['message_id']}; "
                          "connectivity analysis not implemented yet",
            },
            "extra": {},
        }
        self._validator.validate(reply)
        return reply

    def _error(self, body: Any, rej: _Rejected) -> dict[str, Any]:
        self._error_count += 1
        header = body if isinstance(body, dict) else {}
        incident_id = _str_or(header.get("incident_id"), "UNKNOWN")
        offending_id = _str_or(header.get("message_id"), None)
        error = {
            "protocol": PROTOCOL,
            "message_id": f"{incident_id}-CONN-ERR-{self._error_count:02d}",
            "incident_id": incident_id,
            "in_reply_to": offending_id,
            "sender": AGENT_ID,
            "recipient": _str_or(header.get("sender"), "orchestrator"),
            "type": "ERROR",
            "timestamp": _now(),
            "payload": {
                "code": rej.code,
                "detail": rej.detail,
                "field_path": rej.field_path,
                "offending_message_id": offending_id,
            },
            "extra": {},
        }
        self._validator.validate(error)
        return error


def create_app(log_path: str | Path | None = None) -> FastAPI:
    path = log_path if log_path is not None else os.environ.get("NETCOOP_CONNECTIVITY_DB", ":memory:")
    service = ConnectivityService(EventLog(path))
    api = FastAPI(title="NetCoop Connectivity Agent", version="1.0")
    api.state.service = service

    @api.get("/card")
    def card() -> dict[str, Any]:
        return CARD

    @api.post("/messages")
    async def messages(request: Request) -> JSONResponse:
        raw = (await request.body()).decode("utf-8", "replace")
        status, body = service.handle(raw)
        return JSONResponse(body, status_code=status)

    @api.get("/events")
    def events() -> list[dict[str, Any]]:
        return service.log.rows()

    return api


app = create_app()
