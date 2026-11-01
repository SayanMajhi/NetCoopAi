"""Connectivity Agent as a separate REST process.

POST /messages takes a Bundle (schemas/bundle-1.0.json): `messages[0]` is the TASK
addressed to this agent, the rest are the peer FINDINGs it may use, plus the
`observations` and `topology` they cite. The reply is a Bundle too, holding what
connectivity_service/logic.py concludes (FINDINGs, or a RESPONSE when it cannot judge).

Bodies are validated with jsonschema against schemas/bundle-1.0.json and, per
message, schemas/message-1.0.json (format checker on), not the Python models, so
the wire contract is what is actually enforced here. A bad Bundle or a bad TASK
gets HTTP 400 and a Bundle holding a typed ERROR; a bad or duplicate peer message
is dropped with an ERROR in the reply while the rest is still processed. Every message
and observation is then checked against the typed models too (they catch what Python's
jsonschema lets through, e.g. a trailing newline under a `$` pattern), and a peer FINDING
citing evidence that is not in the Bundle gets UNKNOWN_EVIDENCE. A bad input is never
logged as valid and never reported as the agent's own failure.

Each Bundle is one delivery, like one orchestrator run: duplicate message IDs are
detected inside a Bundle, and reply IDs restart at CONN-01, so re-running the same
fixture (or its --control twin, which reuses the TASK ID) gets the same answer.

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

from connectivity_service.logic import handle_bundle
from netcoop.orchestrator.eventlog import EventLog
from pydantic import ValidationError as ModelError

from netcoop.protocol.models import PAYLOAD_MODELS, Envelope, Observation

AGENT_ID = "connectivity_agent"
PROTOCOL = "netcoop/1.0"
SUPPORTED_MAJOR = 1
SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"
_VERSION_RE = re.compile(r"^netcoop/(\d+)\.(\d+)$")

CARD: dict[str, Any] = {
    "agent_id": AGENT_ID,
    "endpoint": "http://127.0.0.1:8001",
    "protocol_versions": [PROTOCOL],
    "capabilities": ["analyze_connectivity", "interpret_peer_findings"],
    "owned_metrics": ["dns_success", "dhcp_success", "reachability"],
    "hypotheses": ["dns_failure", "dhcp_failure", "upstream_dependency", "healthy",
                   "link_congestion", "interface_fault", "device_overload", "hardware_failure"],
}


class _Rejected(Exception):
    def __init__(self, code: str, detail: str, field_path: Optional[str], status: int = 400) -> None:
        super().__init__(detail)
        self.code, self.detail, self.field_path, self.status = code, detail, field_path, status


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _str_or(value: Any, default: Optional[str]) -> Optional[str]:
    return value if isinstance(value, str) and value else default


def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def _field_path(error: ValidationError, prefix: str = "") -> Optional[str]:
    parts = [str(p) for p in error.absolute_path]
    if error.validator == "required" and isinstance(error.instance, dict):
        missing = [k for k in error.validator_value if k not in error.instance]
        parts += missing[:1]
    return ".".join(([prefix] if prefix else []) + parts) or None


def _schema_rejection(error: ValidationError, prefix: str = "") -> _Rejected:
    code = "MISSING_FIELD" if error.validator == "required" else "INVALID_VALUE"
    return _Rejected(code, error.message, _field_path(error, prefix))


def _model_rejection(exc: ModelError, prefix: str, body: Any = None) -> _Rejected:
    """The typed models are stricter than Python's jsonschema in a few corners (e.g. a regex
    `$` that tolerates a trailing newline), so they run second. Map their error to a typed one."""
    error, path = exc.errors()[0], [str(p) for p in exc.errors()[0]["loc"]]
    if isinstance(body, dict) and isinstance(body.get("payload"), dict) and body.get("type") in PAYLOAD_MODELS:
        try:  # payload errors surface from the Envelope's validator without a path; find it
            PAYLOAD_MODELS[body["type"]].model_validate(body["payload"])
        except ModelError as inner:
            error, path = inner.errors()[0], ["payload", *(str(p) for p in inner.errors()[0]["loc"])]
    code = "MISSING_FIELD" if error["type"] == "missing" else "INVALID_VALUE"
    return _Rejected(code, error["msg"], ".".join(([prefix] if prefix else []) + path) or prefix or None)


def _first_message(body: Any) -> Any:
    """messages[0] if there is one, so an ERROR can be addressed to its sender."""
    messages = body.get("messages") if isinstance(body, dict) else None
    return messages[0] if isinstance(messages, list) and messages else None


class _Delivery:
    """State for one Bundle: messages seen and the per-request error counter."""

    def __init__(self) -> None:
        self.seen: set[str] = set()
        self.errors: list[dict[str, Any]] = []
        self.error_count = 0


class ConnectivityService:
    def __init__(self, log: EventLog) -> None:
        self.log = log
        self._bundle = _validator("bundle-1.0.json")
        self._message = _validator("message-1.0.json")
        self._envelope_fields = set(self._message.schema["properties"])

    def handle(self, raw_text: str) -> tuple[int, dict[str, Any]]:
        """One Bundle in, (HTTP status, reply Bundle) out. Never raises on bad input."""
        delivery = _Delivery()
        body: Any = None
        try:
            body = self._parse(raw_text)
            raw_messages = self._check_bundle(body)
        except _Rejected as rej:
            self.log.record(body, valid=False, error=f"{rej.code}: {rej.detail}", body_json=raw_text)
            return rej.status, {"messages": [self._error(delivery, _first_message(body), rej)]}

        evidence = {o["evidence_id"] for o in body.get("observations") or [] if isinstance(o, dict)}
        accepted: list[dict[str, Any]] = []
        for i, raw in enumerate(raw_messages):
            message = self._accept(delivery, raw, i, evidence)
            if message is None and i == 0:  # nothing to do without a valid TASK
                return 400, {"messages": delivery.errors}
            if message is not None:
                accepted.append(message)
        try:
            replies = self._reply(accepted[0], accepted[1:], body)
        except _Rejected as rej:
            return rej.status, {"messages": [self._error(delivery, accepted[0], rej), *delivery.errors]}
        return 200, {"messages": replies + delivery.errors}

    # ----------------------------------------------------------------- validation

    @staticmethod
    def _parse(raw_text: str) -> dict[str, Any]:
        try:
            body = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise _Rejected("MALFORMED_JSON", f"body is not valid JSON: {exc}", None) from exc
        if not isinstance(body, dict):
            raise _Rejected("MALFORMED_JSON", "body must be a JSON object (a Bundle)", None)
        return body

    def _check_bundle(self, body: dict[str, Any]) -> list[Any]:
        """Bundle shape, observations and topology. Messages are checked one by one
        afterwards, so a bad peer message cannot sink the whole delivery."""
        if "messages" not in body:
            raise _Rejected("MISSING_FIELD", "a Bundle needs 'messages' (TASK first, then peer messages)",
                            "messages")
        messages = body["messages"]
        if not isinstance(messages, list) or not messages:
            raise _Rejected("INVALID_VALUE", "'messages' must be a non-empty list, TASK first", "messages")
        error = best_match(self._bundle.iter_errors({**body, "messages": []}))
        if error is not None:
            raise _schema_rejection(error)
        for i, obs in enumerate(body.get("observations") or []):
            try:
                Observation.model_validate(obs)
            except ModelError as exc:
                raise _model_rejection(exc, f"observations.{i}") from exc
        return messages

    def _accept(self, delivery: _Delivery, raw: Any, index: int, evidence: set[str]) -> Optional[dict[str, Any]]:
        """Validate and log messages[index]; a rejection becomes an ERROR in the reply."""
        prefix = f"messages.{index}"
        try:
            message = self._check(raw, prefix)
            if index == 0:
                self._check_task(message, prefix)
            elif message["type"] == "FINDING":
                unknown = sorted(set(message["payload"]["evidence_ids"]) - evidence)
                if unknown:
                    raise _Rejected("UNKNOWN_EVIDENCE", f"cites evidence not in this Bundle's observations: {unknown}",
                                    f"{prefix}.payload.evidence_ids")
            if message["message_id"] in delivery.seen:
                raise _Rejected("DUPLICATE_MESSAGE",
                                f"message_id {message['message_id']} appears twice in this Bundle; ignored",
                                f"{prefix}.message_id", 409)
        except _Rejected as rej:
            self.log.record(raw, valid=False, error=f"{rej.code}: {rej.detail}")
            delivery.errors.append(self._error(delivery, raw, rej))
            return None
        delivery.seen.add(message["message_id"])
        self.log.record(raw, valid=True)
        return message

    def _check(self, body: Any, prefix: str) -> dict[str, Any]:
        """Version gate, move unknown 1.x fields into `extra`, then JSON Schema validation."""
        if not isinstance(body, dict):
            raise _Rejected("INVALID_VALUE", "each message must be a JSON object", prefix)
        protocol = body.get("protocol")
        match = _VERSION_RE.match(protocol) if isinstance(protocol, str) else None
        if match and int(match.group(1)) != SUPPORTED_MAJOR:
            raise _Rejected("SCHEMA_VERSION_UNSUPPORTED",
                            f"{protocol} not supported; accepts netcoop/{SUPPORTED_MAJOR}.x",
                            f"{prefix}.protocol")
        message = {k: v for k, v in body.items() if k in self._envelope_fields}
        unknown = {k: v for k, v in body.items() if k not in self._envelope_fields}
        if unknown:
            message["extra"] = {**(body.get("extra") or {}), **unknown}
        error = best_match(self._message.iter_errors(message))
        if error is not None:
            raise _schema_rejection(error, prefix)
        try:
            Envelope.model_validate(message)
        except ModelError as exc:
            raise _model_rejection(exc, prefix, message) from exc
        return message

    @staticmethod
    def _check_task(message: dict[str, Any], prefix: str) -> None:
        if message["type"] != "TASK":
            raise _Rejected("INVALID_VALUE", f"messages[0] must be the TASK, got {message['type']}",
                            f"{prefix}.type")
        if message["recipient"] != AGENT_ID:
            raise _Rejected("UNKNOWN_RECIPIENT", f"TASK is addressed to {message['recipient']}, not {AGENT_ID}",
                            f"{prefix}.recipient")

    # ----------------------------------------------------------------- replies

    def _reply(self, task: dict[str, Any], peers: list[dict[str, Any]], body: dict[str, Any]) -> list[dict[str, Any]]:
        """Run the connectivity analysis (logic.py) on the accepted messages; check what it sends."""
        try:
            messages = [Envelope.model_validate(m) for m in [task, *peers]]
            observations = [Observation.model_validate(o) for o in body.get("observations") or []]
            replies = [m.model_dump(mode="json") for m in handle_bundle(messages, observations,
                                                                         body.get("topology") or {})]
            for reply in replies:
                self._message.validate(reply)
        except Exception as exc:  # an analysis bug must come back as a typed ERROR, not a 500 page
            raise _Rejected("AGENT_FAILURE", f"{type(exc).__name__}: {exc}", None, 500) from exc
        return replies

    def _error(self, delivery: _Delivery, body: Any, rej: _Rejected) -> dict[str, Any]:
        delivery.error_count += 1
        header = body if isinstance(body, dict) else {}
        incident_id = _str_or(header.get("incident_id"), "UNKNOWN")
        offending_id = _str_or(header.get("message_id"), None)
        error = {
            "protocol": PROTOCOL,
            "message_id": f"{incident_id}-CONN-ERR-{delivery.error_count:02d}",
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
        self._message.validate(error)
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
