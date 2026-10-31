"""Version negotiation at every receive boundary.

- netcoop/1.x : accepted; unknown top-level fields moved into `extra`.
- netcoop/0.9 : upgraded to 1.0 with a visible conversion record.
- anything else (e.g. 2.x): SCHEMA_VERSION_UNSUPPORTED.
"""
from __future__ import annotations

import re
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


def upgrade_0_9(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """TODO(Lead): define the 0.9 dialect (see examples/finding_v0_9.json) and map it.

    Expected shape of the answer: rename old keys (e.g. msg_id -> message_id,
    from -> sender, to -> recipient, kind -> type), fill in_reply_to=None if absent,
    and append one record per conversion, e.g. "0.9 field 'msg_id' -> 'message_id'".
    """
    raise UnsupportedVersion("netcoop/0.9 upgrade not implemented yet")
