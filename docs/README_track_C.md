# Track C — topology, message bus, Connectivity REST service

Owner: Amit (track C). Milestone 1 (data contract / interoperability).
Branch: `m1/amit-topology-bus`.

Track C provides three pieces that the other tracks build on:

| Piece | File | Used by |
|---|---|---|
| **Campus topology** | `scenarios/topology.json` | Connectivity (blast radius), Reporting (affected-ID check), normalizer (device IDs) |
| **Message bus + event log** | `netcoop/orchestrator/bus.py`, `netcoop/orchestrator/eventlog.py` | Orchestrator: every message between agents goes through `MessageBus.deliver()` |
| **Connectivity agent (REST)** | `connectivity_service/app.py` | The one agent that runs as a separate process, so the protocol is a real wire contract |

## Quick start

From the repo root:

```bash
pip install -e ".[dev]"
```

```bash
pytest -q tests/test_topology.py tests/test_bus.py tests/test_connectivity_service.py
```

```bash
uvicorn connectivity_service.app:app --port 8001
```

Then open http://localhost:8001/docs to try the service in the browser.

## 1. Campus topology — `scenarios/topology.json`

14 devices and 13 links, arranged as a tree. Each node has `id`, `type` and `zone`, plus a
readable `label`. Each link has `link_id`, `a`, `b` and `capacity_mbps`.

| ID | Type | Zone | Role |
|---|---|---|---|
| `CORE_RTR_01` | router | core | Campus core router |
| `SRV_DNS_DHCP_01` | server | datacenter | DNS/DHCP server (`services: ["dns", "dhcp"]`) |
| `SW_CAMPUS_1` | distribution_switch | hostel | Hostel distribution |
| `SW_CAMPUS_2` | distribution_switch | department | Department distribution |
| `SW_CAMPUS_3`–`6` | access_switch | hostel | Hostel blocks A–D |
| `SW_CAMPUS_7`–`10` | access_switch | department | CSE, EE, lecture hall, library |
| `HOSTEL_AP_01` | access_point | hostel | Hostel block A Wi-Fi (on `SW_CAMPUS_3`) |
| `DEPT_AP_01` | access_point | department | CSE Wi-Fi (on `SW_CAMPUS_7`) |

```
CORE_RTR_01 ─┬─ HOSTEL_UPLINK_01 ── SW_CAMPUS_1 ─┬─ SW_CAMPUS_3 ── HOSTEL_AP_01
             │                                    ├─ SW_CAMPUS_4
             │                                    ├─ SW_CAMPUS_5
             │                                    └─ SW_CAMPUS_6
             ├─ DEPT_UPLINK_01 ──── SW_CAMPUS_2 ─┬─ SW_CAMPUS_7 ── DEPT_AP_01
             │                                    ├─ SW_CAMPUS_8
             │                                    ├─ SW_CAMPUS_9
             │                                    └─ SW_CAMPUS_10
             └─ SRV_LINK_01 ─────── SRV_DNS_DHCP_01
```

Design decisions:

- **Generator IDs are used as-is.** The organizer generator emits device IDs
  `SW_CAMPUS_1`–`SW_CAMPUS_10` (B3). All ten are topology switches, so any untouched
  generator output maps to a known device without renaming.
- **Links and capacities are ours.** The generator supplies no links. Uplinks are
  1000 Mbps and the server link is 10000 Mbps.
- **Shared upstream for blast radius.** `HOSTEL_UPLINK_01` is the only path from the core
  to every hostel device. Cutting it isolates the whole hostel and nothing else.
- **One uplink per switch.** The generator's `link_utilization_pct` has no link ID. Because
  each switch has exactly one uplink (for example `SW_CAMPUS_5` → `ACC_LINK_05`), the
  normalizer can attribute the metric to that link and record it as a derived step.

`tests/test_topology.py` checks required fields, unique IDs, links pointing at real nodes,
the device count and roles, full connectivity, the shared upstream, the presence of all
generator IDs, and one uplink per switch.

## 2. Message bus — `netcoop/orchestrator/bus.py`

```python
from netcoop.orchestrator.bus import MessageBus
from netcoop.orchestrator.eventlog import EventLog

bus = MessageBus(EventLog("traces/events.sqlite"))   # EventLog() = in memory
envelope, error = bus.deliver(raw)                    # raw: dict, JSON str or bytes
```

`deliver()` returns `(envelope, None)` when the message is accepted and `(None, error_envelope)`
when it is not. It never raises on bad input, and every attempt is logged. Checks, in order:

| Step | Failure → `ErrorCode` |
|---|---|
| Parse JSON (must be an object) | `MALFORMED_JSON` |
| `protocol` present | `MISSING_FIELD` (`field_path: protocol`) |
| Version negotiation via `netcoop/protocol/compat.py` (1.x accepted, unknown fields moved to `extra`, 2.x rejected) | `SCHEMA_VERSION_UNSUPPORTED` |
| Validate against the `Envelope` model and its typed payload | `MISSING_FIELD` / `INVALID_VALUE`, with a `field_path` such as `sender` or `payload.stance` |
| `message_id` already accepted | `DUPLICATE_MESSAGE` (ignored; a rejected ID may be resent once fixed) |

The error is itself a valid `netcoop/1.0` ERROR envelope:

- `message_id`: `<incident_id>-BUS-ERR-01`, `-02`, … (`UNKNOWN` when the incident ID is unreadable)
- `in_reply_to` and `payload.offending_message_id`: the rejected message's ID
- `sender`: `orchestrator`; `recipient`: the original sender
- `payload`: `code`, `detail`, `field_path`

Conversion notes from `compat.py` (for example "accepted netcoop/1.3 as netcoop/1.0") are kept
in `bus.conversions[message_id]`.

## 3. Event log — `netcoop/orchestrator/eventlog.py`

One SQLite table, `events`, with exactly the spec's columns plus an auto-increment `seq`
for arrival order:

```
seq, message_id, incident_id, sender, recipient, type, protocol,
in_reply_to, valid, error, body_json, received_at
```

- `record(body, valid=..., error=..., body_json=...)` writes one row. Header fields are read
  safely from a possibly malformed body; anything missing or not a string is stored as NULL.
- `rows(incident_id=None)` returns rows as dicts in arrival order.
- `body_json` keeps the exact text received, even when it is not valid JSON.
- `received_at` is wall-clock UTC, kept for operations and excluded from reproducibility checks.

## 4. Connectivity agent — `connectivity_service/app.py`

A FastAPI app that runs as its own process:

```bash
uvicorn connectivity_service.app:app --port 8001
```

The event log is in memory by default. To keep it in a file, set `NETCOOP_CONNECTIVITY_DB`.

Bash:

```bash
NETCOOP_CONNECTIVITY_DB=traces/connectivity.sqlite uvicorn connectivity_service.app:app --port 8001
```

PowerShell:

```powershell
$env:NETCOOP_CONNECTIVITY_DB="traces/connectivity.sqlite"; uvicorn connectivity_service.app:app --port 8001
```

| Endpoint | What it does |
|---|---|
| `GET /card` | The agent card (same as `connectivity_agent` in `netcoop/protocol/cards.py`) |
| `POST /messages` | Receives one `netcoop/1.0` message and replies with one message |
| `GET /events` | The event-log rows as JSON |

`POST /messages` validates the body with **jsonschema against `schemas/message-1.0.json`**, not
the Python models, so the generated wire contract is what is enforced. The JSON Schema is
language-neutral, so a non-Python agent could be validated the same way.

| Request | HTTP | Reply |
|---|---|---|
| Valid message | 200 | `RESPONSE` with `in_reply_to` = the received `message_id`, addressed to the sender |
| Not JSON / not an object | 400 | `ERROR` `MALFORMED_JSON` |
| Missing field | 400 | `ERROR` `MISSING_FIELD` + `field_path` |
| Wrong type or enum value | 400 | `ERROR` `INVALID_VALUE` + `field_path` |
| `netcoop/2.x` | 400 | `ERROR` `SCHEMA_VERSION_UNSUPPORTED` |
| Repeated `message_id` | 409 | `ERROR` `DUPLICATE_MESSAGE`; ignored and logged |

Unknown top-level fields from a `1.x` sender are moved into `extra`, as the protocol requires.
Outgoing RESPONSE and ERROR messages are validated against the same schema before they are sent.

Example:

```bash
curl -i -H "Content-Type: application/json" --data @docs/m1_curl/valid_finding.json localhost:8001/messages
```

**Current limit:** the RESPONSE is a stub. Its `answer` says "connectivity analysis not
implemented yet" and `evidence_ids` is empty. Real topology, DNS/DHCP, reachability and
peer-finding analysis is Part 2 work.

## Tests

| File | Tests | Covers |
|---|---|---|
| `tests/test_topology.py` | 8 | Topology integrity, shared upstream, generator IDs, one uplink per switch |
| `tests/test_bus.py` | 13 | Valid, duplicate and invalid messages all logged; v2 unsupported; missing protocol; 1.x unknown fields; malformed JSON; payload field paths; resend after fix; file-backed log |
| `tests/test_connectivity_service.py` | 11 | Card; valid → RESPONSE; missing field → typed ERROR; v2 unsupported; bad enum; malformed JSON; duplicate → 409; QUERY echoes `query_id`; 1.x extra fields; the 3 acceptance rows |

```bash
pytest -q
```

Full suite on this branch: 56 passed (track C plus the protocol, schema-drift and smoke tests).

## Acceptance proof

[`docs/m1_curl_proof.md`](m1_curl_proof.md) has real `curl` output from the running service:
valid → 200 RESPONSE, missing `sender` → typed ERROR, duplicate ID → logged and ignored, plus
the 3 SQLite rows read from the database file. The request bodies are in `docs/m1_curl/`.

## Notes for other tracks

- **A (Sayan):** `HOSTEL_CORE_SW_01` is now `SW_CAMPUS_1`. `examples/report_diagnosed.json`
  and `examples/task.json` still use the old ID. `HOSTEL_UPLINK_01` is unchanged. When the 0.9
  upgrade in `compat.py` is implemented, the bus uses it automatically.
- **B:** every generator device ID exists in the topology. For `link_utilization_pct`, use the
  switch's single uplink as `link_id` and flag it `derived`.
- **Orchestrator (Part 2):** create one `MessageBus` per run and pass every inter-agent message
  through `deliver()`. Send ERROR envelopes into the trace instead of dropping them. Call the
  Connectivity agent at the `endpoint` in its card (`http://127.0.0.1:8001`).
