"""SQLite event log: one row per message the bus receives, valid or not."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_COLUMNS = (
    "message_id", "incident_id", "sender", "recipient", "type", "protocol",
    "in_reply_to", "valid", "error", "body_json", "received_at",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id   TEXT,
    incident_id  TEXT,
    sender       TEXT,
    recipient    TEXT,
    type         TEXT,
    protocol     TEXT,
    in_reply_to  TEXT,
    valid        INTEGER NOT NULL,
    error        TEXT,
    body_json    TEXT NOT NULL,
    received_at  TEXT NOT NULL
)
"""


def _text(body: Any, key: str) -> Optional[str]:
    """Read a header field from a possibly malformed body; non-strings become None."""
    if isinstance(body, dict) and isinstance(body.get(key), str):
        return body[key]
    return None


class EventLog:
    """Append-only log. Use ':memory:' for tests, a file path for real runs."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def record(self, body: Any, *, valid: bool, error: Optional[str] = None,
               body_json: Optional[str] = None) -> None:
        """Log one received message. `body` is the parsed dict if parsing worked;
        `body_json` is the raw text (defaults to json.dumps(body))."""
        if body_json is None:
            body_json = json.dumps(body, default=str, sort_keys=True)
        row = (
            _text(body, "message_id"), _text(body, "incident_id"), _text(body, "sender"),
            _text(body, "recipient"), _text(body, "type"), _text(body, "protocol"),
            _text(body, "in_reply_to"), int(valid), error, body_json,
            datetime.now(timezone.utc).isoformat(),
        )
        self._conn.execute(
            f"INSERT INTO events ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' * len(_COLUMNS))})",
            row,
        )
        self._conn.commit()

    def rows(self, incident_id: Optional[str] = None) -> list[dict[str, Any]]:
        """All rows in arrival order, optionally for one incident."""
        sql = f"SELECT seq, {', '.join(_COLUMNS)} FROM events"
        params: tuple = ()
        if incident_id is not None:
            sql += " WHERE incident_id = ?"
            params = (incident_id,)
        return [dict(r) for r in self._conn.execute(sql + " ORDER BY seq", params)]

    def close(self) -> None:
        self._conn.close()
