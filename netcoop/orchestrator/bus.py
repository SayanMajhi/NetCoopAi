"""Message bus: validates every incoming message, drops duplicates, and turns
every failure into a typed ERROR envelope. Nothing is dropped silently — every
delivery attempt is written to the event log."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from pydantic import ValidationError

from netcoop.orchestrator.eventlog import EventLog
from netcoop.protocol.compat import UnsupportedVersion, upgrade
from netcoop.protocol.models import (
    PAYLOAD_MODELS,
    PROTOCOL_VERSION,
    Envelope,
    ErrorCode,
    ErrorPayload,
    MessageType,
)

BUS_ID = "orchestrator"

DeliverResult = tuple[Optional[Envelope], Optional[Envelope]]


class _Rejected(Exception):
    def __init__(self, code: ErrorCode, detail: str, field_path: Optional[str] = None) -> None:
        super().__init__(detail)
        self.code, self.detail, self.field_path = code, detail, field_path


class MessageBus:
    """Version negotiation is delegated to protocol/compat.py; any conversion
    records it produces are kept in `conversions`, keyed by message_id."""

    def __init__(self, log: Optional[EventLog] = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.log = log or EventLog()
        self._clock = clock
        self._seen: set[str] = set()
        self._error_count = 0
        self.conversions: dict[str, list[str]] = {}

    def deliver(self, raw: str | bytes | dict[str, Any]) -> DeliverResult:
        """Returns (envelope, None) when accepted, (None, error_envelope) otherwise."""
        body: Any = raw
        raw_text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else (
            raw if isinstance(raw, str) else None)
        try:
            body = _parse(raw)
            upgraded, records = _negotiate(body)
            envelope = _validate(upgraded)
            if envelope.message_id in self._seen:
                raise _Rejected(ErrorCode.DUPLICATE_MESSAGE,
                                f"message_id {envelope.message_id} already delivered; ignored",
                                "message_id")
        except _Rejected as rej:
            self.log.record(body, valid=False, error=f"{rej.code.value}: {rej.detail}",
                            body_json=raw_text)
            return None, self._error_envelope(body, rej)

        self._seen.add(envelope.message_id)
        if records:
            self.conversions[envelope.message_id] = records
        self.log.record(body, valid=True, body_json=raw_text)
        return envelope, None

    def _error_envelope(self, body: Any, rej: _Rejected) -> Envelope:
        self._error_count += 1
        header = body if isinstance(body, dict) else {}
        incident_id = _str_or(header.get("incident_id"), "UNKNOWN")
        offending_id = _str_or(header.get("message_id"), None)
        payload = ErrorPayload(code=rej.code, detail=rej.detail, field_path=rej.field_path,
                               offending_message_id=offending_id)
        return Envelope(
            protocol=PROTOCOL_VERSION,
            message_id=f"{incident_id}-BUS-ERR-{self._error_count:02d}",
            incident_id=incident_id,
            in_reply_to=offending_id,
            sender=BUS_ID,
            recipient=_str_or(header.get("sender"), BUS_ID),
            type=MessageType.ERROR,
            timestamp=self._clock(),
            payload=payload.model_dump(mode="json"),
        )


def _str_or(value: Any, default: Optional[str]) -> Optional[str]:
    return value if isinstance(value, str) and value else default


def _parse(raw: str | bytes | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
        raise _Rejected(ErrorCode.MALFORMED_JSON, f"body is not valid JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise _Rejected(ErrorCode.MALFORMED_JSON, "body must be a JSON object")
    return body


def _negotiate(body: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    if body.get("protocol") is None:
        raise _Rejected(ErrorCode.MISSING_FIELD, "Field required", "protocol")
    try:
        return upgrade(body)
    except UnsupportedVersion as exc:
        raise _Rejected(ErrorCode.SCHEMA_VERSION_UNSUPPORTED, str(exc), "protocol") from exc


def _validate(body: dict[str, Any]) -> Envelope:
    try:
        return Envelope.model_validate(body)
    except ValidationError as exc:
        error, prefix = exc.errors()[0], ""
        # Payload errors come out of Envelope's model validator with locations relative
        # to the payload; re-validate the payload alone to report e.g. "payload.stance".
        if body.get("type") in PAYLOAD_MODELS and isinstance(body.get("payload"), dict):
            try:
                PAYLOAD_MODELS[body["type"]].model_validate(body["payload"])
            except ValidationError as inner:
                error, prefix = inner.errors()[0], "payload."
        field_path = prefix + ".".join(str(p) for p in error["loc"]) or None
        code = ErrorCode.MISSING_FIELD if error["type"] == "missing" else ErrorCode.INVALID_VALUE
        raise _Rejected(code, error["msg"], field_path) from exc
