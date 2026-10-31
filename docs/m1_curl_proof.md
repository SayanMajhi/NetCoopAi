# M1 proof — Connectivity agent over real HTTP

Real `curl` output against the Connectivity agent running as a **separate process**
(`connectivity_service/app.py`). Incoming bodies are validated with `jsonschema`
against the generated `schemas/message-1.0.json`, not the Python models.
Every request is written to a SQLite event log.

Captured 2026-10-31 19:05 UTC on branch `m1/amit-topology-bus`.
Only the timestamps differ when you re-run it.

| # | Check | Expected | Result |
|---|---|---|---|
| 1 | Valid FINDING | 200 RESPONSE with `in_reply_to` = the sent `message_id` | ✅ 200, `in_reply_to: INC-0001-INF-02` |
| 2 | Missing `sender` | typed ERROR with field path + code | ✅ 400, `MISSING_FIELD`, `field_path: sender` |
| 3 | Duplicate `message_id` | logged and ignored | ✅ 409, `DUPLICATE_MESSAGE`, row stored with `valid = 0` |
| 4 | SQLite rows | 3 rows, one per request | ✅ rows 1–3 below |

## Reproduce

From the repo root, start the service with a file-backed event log.

Bash:

```bash
NETCOOP_CONNECTIVITY_DB=traces/m1_proof.sqlite uvicorn connectivity_service.app:app --port 8001
```

PowerShell:

```powershell
$env:NETCOOP_CONNECTIVITY_DB="traces/m1_proof.sqlite"; uvicorn connectivity_service.app:app --port 8001
```

In a second terminal, run the requests below in order. The request bodies are
`docs/m1_curl/valid_finding.json`, which is `examples/finding.json` addressed to
`connectivity_agent`, and `docs/m1_curl/missing_sender.json`, which is the same
message with `message_id` `INC-0001-INF-03` and no `sender`. In PowerShell, use
`curl.exe` instead of `curl`.

## 0. Agent card

```bash
curl -i localhost:8001/card
```

```http
HTTP/1.1 200 OK
date: Sat, 31 Oct 2026 19:05:41 GMT
server: uvicorn
content-length: 308
content-type: application/json

{"agent_id":"connectivity_agent","endpoint":"http://127.0.0.1:8001","protocol_versions":["netcoop/1.0"],"capabilities":["analyze_connectivity","interpret_peer_findings"],"owned_metrics":["dns_success","dhcp_success","reachability"],"hypotheses":["dns_failure","dhcp_failure","upstream_dependency","healthy"]}
```

## 1. Valid FINDING → 200 RESPONSE

```bash
curl -i -H "Content-Type: application/json" --data @docs/m1_curl/valid_finding.json localhost:8001/messages
```

```http
HTTP/1.1 200 OK
date: Sat, 31 Oct 2026 19:05:41 GMT
server: uvicorn
content-length: 377
content-type: application/json

{"protocol":"netcoop/1.0","message_id":"INC-0001-CONN-01","incident_id":"INC-0001","in_reply_to":"INC-0001-INF-02","sender":"connectivity_agent","recipient":"infrastructure_agent","type":"RESPONSE","timestamp":"2026-10-31T19:05:41Z","payload":{"query_id":null,"evidence_ids":[],"answer":"received FINDING INC-0001-INF-02; connectivity analysis not implemented yet"},"extra":{}}
```

`in_reply_to` is the FINDING's `message_id`, and the reply is addressed back to the sender
(`infrastructure_agent`). The RESPONSE is a stub until the real connectivity analysis lands.

## 2. Missing `sender` → typed ERROR

```bash
curl -i -H "Content-Type: application/json" --data @docs/m1_curl/missing_sender.json localhost:8001/messages
```

```http
HTTP/1.1 400 Bad Request
date: Sat, 31 Oct 2026 19:05:41 GMT
server: uvicorn
content-length: 378
content-type: application/json

{"protocol":"netcoop/1.0","message_id":"INC-0001-CONN-ERR-01","incident_id":"INC-0001","in_reply_to":"INC-0001-INF-03","sender":"connectivity_agent","recipient":"orchestrator","type":"ERROR","timestamp":"2026-10-31T19:05:41Z","payload":{"code":"MISSING_FIELD","detail":"'sender' is a required property","field_path":"sender","offending_message_id":"INC-0001-INF-03"},"extra":{}}
```

The ERROR is itself a schema-valid `netcoop/1.0` message. Its `payload` carries the code
(`MISSING_FIELD`), the field path (`sender`) and the rejected message's ID. Because the
sender is unknown, the reply goes to `orchestrator`.

## 3. Duplicate `message_id` → logged and ignored

The valid FINDING from step 1 is sent again:

```bash
curl -i -H "Content-Type: application/json" --data @docs/m1_curl/valid_finding.json localhost:8001/messages
```

```http
HTTP/1.1 409 Conflict
date: Sat, 31 Oct 2026 19:05:42 GMT
server: uvicorn
content-length: 415
content-type: application/json

{"protocol":"netcoop/1.0","message_id":"INC-0001-CONN-ERR-02","incident_id":"INC-0001","in_reply_to":"INC-0001-INF-02","sender":"connectivity_agent","recipient":"infrastructure_agent","type":"ERROR","timestamp":"2026-10-31T19:05:42Z","payload":{"code":"DUPLICATE_MESSAGE","detail":"message_id INC-0001-INF-02 already received; ignored","field_path":"message_id","offending_message_id":"INC-0001-INF-02"},"extra":{}}
```

No second RESPONSE is produced. The repeat is recorded as row 3 below.

## 4. The 3 SQLite rows

Read straight from the database file (Python is used because the `sqlite3` CLI is not
installed on every machine):

```bash
python -c "import sqlite3; c=sqlite3.connect('traces/m1_proof.sqlite'); [print(' | '.join('NULL' if v is None else str(v) for v in r)) for r in c.execute('SELECT seq, message_id, incident_id, sender, recipient, type, protocol, in_reply_to, valid, error, received_at FROM events ORDER BY seq')]"
```

| seq | message_id | incident_id | sender | recipient | type | protocol | in_reply_to | valid | error | received_at |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | INC-0001-INF-02 | INC-0001 | infrastructure_agent | connectivity_agent | FINDING | netcoop/1.0 | INC-0001-ORCH-01 | 1 | NULL | 2026-10-31T19:05:41.626098+00:00 |
| 2 | INC-0001-INF-03 | INC-0001 | NULL | connectivity_agent | FINDING | netcoop/1.0 | INC-0001-ORCH-01 | 0 | MISSING_FIELD: 'sender' is a required property | 2026-10-31T19:05:41.888661+00:00 |
| 3 | INC-0001-INF-02 | INC-0001 | infrastructure_agent | connectivity_agent | FINDING | netcoop/1.0 | INC-0001-ORCH-01 | 0 | DUPLICATE_MESSAGE: message_id INC-0001-INF-02 already received; ignored | 2026-10-31T19:05:42.182454+00:00 |

The table also stores the exact request body in a `body_json` column, which is omitted
here for width. `GET localhost:8001/events` returns the same rows as JSON.

## Same checks as automated tests

`tests/test_connectivity_service.py` covers each case above, including
`test_acceptance_three_rows_logged`, which asserts these three rows against a
temporary SQLite file. The message-bus side of this proof (valid, duplicate and
invalid deliveries all logged; v2 unsupported) is covered in `tests/test_bus.py`.

```bash
pytest -q
```
