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
| `POST /messages` | Receives a **Bundle** and replies with a Bundle (Phase 2, C2) |
| `GET /events` | The event-log rows as JSON |

A Bundle (`schemas/bundle-1.0.json`) is `{"messages": [...], "observations": [...], "topology": {...}}`.
`messages[0]` is the TASK addressed to `connectivity_agent`; the rest are the peer FINDINGs
it may use. The service validates with **jsonschema** (format checker on): the Bundle against
`schemas/bundle-1.0.json` and each message against `schemas/message-1.0.json`, not the Python
models, so the generated wire contract is what is enforced.

| Request | HTTP | Reply Bundle holds |
|---|---|---|
| Valid Bundle | 200 | `RESPONSE` to the TASK (`in_reply_to` = TASK ID, addressed to its sender) |
| Not JSON / not an object | 400 | `ERROR` `MALFORMED_JSON` |
| No `messages` (e.g. a bare envelope) / empty list | 400 | `ERROR` `MISSING_FIELD` / `INVALID_VALUE`, `field_path` `messages` |
| Bad observation / unknown top-level field | 400 | `ERROR` `INVALID_VALUE` + `field_path` (e.g. `observations.0.observed_at`) |
| Bad TASK (missing field, bad value, `2.x`) | 400 | typed `ERROR`, `field_path` like `messages.0.sender` |
| `messages[0]` not a TASK / TASK for another agent | 400 | `ERROR` `INVALID_VALUE` / `UNKNOWN_RECIPIENT` |
| Bad or `2.x` peer message | 200 | `RESPONSE` to the TASK **plus** an `ERROR` for that peer (`messages.N...`); the peer is dropped |
| Same `message_id` twice in one Bundle | 200 | `ERROR` `DUPLICATE_MESSAGE` for the repeat; ignored and logged |

Every message in the Bundle gets its own event-log row. Unknown top-level fields on a `1.x`
message are moved into `extra`. Outgoing messages are validated against the schema before they
are sent; the RESPONSE carries the TASK's timestamp (the incident clock) so runs are reproducible.

**Each Bundle is one delivery.** Message IDs are deterministic per fixture, so a re-run (or the
`--control` run, which reuses the TASK ID) sends the same IDs again. Duplicates are therefore
detected inside a Bundle, and reply IDs restart at `CONN-01` for every request.

Example (from the repo root; `curl.exe` in PowerShell):

```bash
curl -i -H "Content-Type: application/json" --data @docs/m2_curl/bundle.json localhost:8001/messages
```

`docs/m2_curl/bundle_bad_peer.json` is the same Bundle with `stance` removed from the peer FINDING.
The Phase 1 proof (`docs/m1_curl/`) used single messages and no longer matches the endpoint.

### Connectivity reasoning — `connectivity_service/logic.py` (C3)

`handle_bundle(messages, observations, topology)` is the agent (`connectivity_agent`, IDs `CONN`).
It reads the topology as a tree (each link runs from `a`, towards the core, to `b`) and never
needs thresholds: the checks it owns are booleans. Missing, stale or malformed checks are unknown.

| Evidence | Finding |
|---|---|
| `dns_success` / `dhcp_success` false on clients, server (`services` in topology) reachable | `dns_failure` / `dhcp_failure@SRV_DNS_DHCP_01` supports (0.8 for ≥ 2 clients and server known reachable, else 0.6) |
| Same, but the server itself is unreachable | `upstream_dependency@SRV_LINK_01` **contradicts** `dns_failure` / `dhcp_failure` |
| Every client's check succeeds | `healthy@SRV_DNS_DHCP_01` **contradicts** `dns_failure` / `dhcp_failure` |
| Devices unreachable, all behind one link | `upstream_dependency@<link>` (or `@<switch>` when the switch just below still answers), with the blast radius |
| **Peer FINDINGs** naming faults on topology elements | `upstream_dependency@<deepest link they all share>` with blast radius and `consumed_message_ids`; 0.7 if two agents agree, 0.5 if one, 0.4 per link when the faults share no upstream |
| No peer faults, every own check passes | `healthy@<common upstream of the TASK scope>` |
| No checks of its own and no usable peers | a `RESPONSE` saying it cannot judge |

So the peers change the conclusion: on congestion the normal run says
`upstream_dependency@HOSTEL_UPLINK_01` (consuming the Performance/Infrastructure findings), the
`--control` run says `healthy@HOSTEL_UPLINK_01`. Peer evidence IDs that are not in the Bundle are
never cited. If the analysis crashes, the service answers HTTP 500 with a typed `AGENT_FAILURE` ERROR.

### Remote client — `netcoop/orchestrator/remote.py` (C4)

`send_bundle(bundle, endpoint, *, timeout=5.0, retries=1)` is how the orchestrator reaches the
Connectivity process (`netcoop run` without `--local`; `--endpoint` overrides the card's
`http://127.0.0.1:8001`). It posts the Bundle to `<endpoint>/messages` with `httpx` and returns a
`ConnectivityReply`: the raw reply messages (the bus validates each one), the raw request and
response bodies for `traces/<incident>.jsonl`, and the HTTP status. It **never raises**.

| What happens | Retried? | `error` |
|---|---|---|
| 2xx Bundle | — | `None` |
| Connection refused / timeout | once | `AGENT_TIMEOUT: connectivity_unreachable: ...` → report limitation, status `insufficient_evidence` |
| 502 / 503 / 504 | once | as the final reply |
| 4xx / 500 with an ERROR Bundle | no (the agent's typed answer) | `AGENT_FAILURE: HTTP <n>: <code>: <detail>`; the ERRORs still go through the bus |
| Not JSON / not a Bundle | no | `AGENT_FAILURE: HTTP <n>: ...`; the raw body is kept for the trace |
| Anything unexpected | no | `AGENT_FAILURE: <exception>` |

## Tests

| File | Tests | Covers |
|---|---|---|
| `tests/test_topology.py` | 8 | Topology integrity, shared upstream, generator IDs, one uplink per switch |
| `tests/test_bus.py` | 13 | Valid, duplicate and invalid messages all logged; v2 unsupported; missing protocol; 1.x unknown fields; malformed JSON; payload field paths; resend after fix; file-backed log |
| `tests/test_connectivity_service.py` | 22 | Card; Bundle in/out; reply uses incident clock and repeats identically; bare envelope, empty messages, bad observation, unknown field rejected; TASK checks (missing field, not a TASK, wrong recipient, bad timestamp, v2); bad/v2/duplicate peers dropped with ERROR while the TASK is answered; 1.x extra fields; every message logged; analysis crash → `AGENT_FAILURE` |
| `tests/test_connectivity_logic.py` | 17 | Topology upstream/blast radius; peers localise to the shared uplink and are consumed; control run differs; independent faults; contradicting/healthy/unknown peers; DNS/DHCP failure and server unreachable; unreachable devices; missing ≠ failure; determinism; real pipeline normal vs control |
| `tests/test_remote.py` | 15 | Real service round trip keeps both bodies; refused/timeout retried once then `AGENT_TIMEOUT` (`connectivity_unreachable`); retry recovers; 503 retried, 400/500 not; non-JSON and non-Bundle replies; never raises; pipeline over HTTP traces bodies and control differs; unreachable agent → `insufficient_evidence` |

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
