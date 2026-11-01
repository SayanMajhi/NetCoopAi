"""Version negotiation at every receive boundary.

- netcoop/1.x : accepted; unknown top-level fields moved into `extra`.
- netcoop/0.9 : upgraded to 1.0 with a visible conversion record.
- anything else (e.g. 2.x): SCHEMA_VERSION_UNSUPPORTED.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from netcoop.protocol.models import PROTOCOL_VERSION, Envelope

ENVELOPE_FIELDS = set(Envelope.model_fields)
_VERSION = re.compile(r"^netcoop/(\d+)\.(\d+)$")


class UnsupportedVersion(ValueError):
    pass


def parse_version(protocol: Any) -> tuple[int, int]:
    match = _VERSION.match(str(protocol or ""))
    if not match:
        raise UnsupportedVersion(f"unrecognized protocol value {protocol!r}")
    return int(match.group(1)), int(match.group(2))


def upgrade(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return (1.0-shaped dict, conversion records). Raises UnsupportedVersion."""
    major, minor = parse_version(raw.get("protocol"))
    if (major, minor) == (0, 9):
        return upgrade_0_9(raw)
    if major != 1:
        raise UnsupportedVersion(f"netcoop/{major}.{minor} not supported (accepts 1.x, 0.9)")
    records: list[str] = []
    out = {k: v for k, v in raw.items() if k in ENVELOPE_FIELDS}
    unknown = {k: v for k, v in raw.items() if k not in ENVELOPE_FIELDS}
    if unknown:
        out["extra"] = {**raw.get("extra", {}), **unknown}
        records.append(f"preserved unknown fields in extra: {sorted(unknown)}")
    if minor != 0:
        out["protocol"] = PROTOCOL_VERSION
        records.append(f"accepted netcoop/1.{minor} as {PROTOCOL_VERSION}")
    return out, records


# The netcoop/0.9 dialect (examples/finding_v0_9.json): envelope keys and a FINDING body.
_ENVELOPE_0_9 = {"msg_id": "message_id", "incident": "incident_id", "from": "sender", "to": "recipient",
                 "reply_to": "in_reply_to"}
_FINDING_0_9 = {"id": "finding_id", "evidence": "evidence_ids", "why": "reason"}


def upgrade_0_9(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Map a netcoop/0.9 message to 1.0, one conversion record per change. Only the 0.9
    FINDING was ever deployed; other 0.9 kinds are refused rather than guessed."""
    records: list[str] = [f"upgraded netcoop/0.9 to {PROTOCOL_VERSION}"]
    out: dict[str, Any] = {"protocol": PROTOCOL_VERSION}
    for old, new in _ENVELOPE_0_9.items():
        if old in raw:
            out[new] = raw[old]
            records.append(f"0.9 field '{old}' -> '{new}'")
    out.setdefault("in_reply_to", None)
    kind = str(raw.get("kind", "")).upper()
    if kind != "FINDING":
        raise UnsupportedVersion(f"netcoop/0.9 kind {raw.get('kind')!r} has no 1.0 mapping (only 'finding')")
    out["type"] = kind
    records.append(f"0.9 field 'kind' {raw.get('kind')!r} -> 'type' {kind!r}")
    ts = raw.get("ts")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        out["timestamp"] = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        records.append(f"0.9 field 'ts' {ts} (Unix seconds) -> 'timestamp' {out['timestamp']}")
    elif "ts" in raw:
        out["timestamp"] = raw["ts"]           # left for validation to reject with a field path

    body = raw.get("body") if isinstance(raw.get("body"), dict) else {}
    payload: dict[str, Any] = {}
    for old, new in _FINDING_0_9.items():
        if old in body:
            payload[new] = body[old]
            records.append(f"0.9 body '{old}' -> payload '{new}'")
    if "cause" in body and "target" in body:
        payload["hypothesis"] = payload["target_hypothesis"] = f"{body['cause']}@{body['target']}"
        records.append(f"0.9 body 'cause' + 'target' -> payload 'hypothesis' {payload['hypothesis']!r}")
    if isinstance(body.get("supports"), bool):
        payload["stance"] = "supports" if body["supports"] else "contradicts"
        records.append(f"0.9 body 'supports' {body['supports']} -> payload 'stance' {payload['stance']!r}")
    out["payload"] = payload
    leftovers = sorted(set(raw) - set(_ENVELOPE_0_9) - {"protocol", "kind", "ts", "body"})
    if leftovers:
        out["extra"] = {k: raw[k] for k in leftovers}
        records.append(f"preserved unknown 0.9 fields in extra: {leftovers}")
    return out, records
