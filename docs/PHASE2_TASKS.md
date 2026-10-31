# Phase 2 (Milestone M2) — Specialist agents and interoperability proof

**Window:** T+7h → T+11h (replanned; we are ~4h behind). **Repo:** https://github.com/SayanMajhi/NetCoopAi, base `contract-1.0.1`.
**Goal (ARCHITECTURE.md §7 "Interoperability proven"):** Performance and Infrastructure analyse canonical evidence and emit schema-valid FINDINGs; the Connectivity agent, running as its **own REST process**, receives those findings, uses them, and changes its conclusion; removing them (`--control`) gives a different conclusion. `netcoop run <scenario>` runs end to end and always returns a schema-valid report plus a trace.
**Not in Phase 2:** confidence scoring, decision gates, QUERY rounds, 0.9 upgrade, report actions (Phase 3). Until then the pipeline ends in `insufficient_evidence` with the findings listed.

Rules unchanged: one branch per task (`m2/<name>-<topic>`), PR + 1 review, `pytest -q` green on `main`. Only the Lead edits `netcoop/protocol/models.py`; if a model must change, ask first (new contract tag).

## Decisions already made (Lead) — everyone builds to these

**D1. Scenario fixture format** (`scenarios/dev/*.json`) — a superset of the organizer generator format, so the same adapter reads both:
```json
{
  "incident_id": "INC-0101",
  "window_end": "2026-11-01T10:05:00Z",
  "complaint": "Hostel block A users report slow internet",
  "records": [
    {"timestamp": "2026-11-01T10:01:00Z", "device_id": "SW_CAMPUS_1", "source_id": "switch-telemetry",
     "metrics": {"cpu_utilization_pct": 41.0, "queue_drops": 1200}},
    {"timestamp": "2026-11-01T10:01:00Z", "link_id": "HOSTEL_UPLINK_01", "source_id": "switch-telemetry",
     "metrics": {"link_utilization_pct": 96.2, "throughput_mbps": 962}},
    {"timestamp": "2026-11-01T10:01:00Z", "device_id": "SW_CAMPUS_3", "source_id": "client-probe",
     "metrics": {"latency_ms": 240, "packet_loss_pct": 1.2, "dns_success": true}}
  ],
  "messages": [],
  "inject": {}
}
```
- `records` use generator keys; `link_id` and `source_id` are optional additions (default `source_id`: `organizer-generator`). A bare generator file (one object) is still accepted.
- New raw aliases the adapter must map: `rtt_sec` → `latency_ms` (×1000), `throughput_kbps` → `throughput_mbps` (÷1000), `retries`/`retransmissions` → `retry_rate_pct`. Every conversion is recorded.
- `messages`: raw protocol messages injected through the bus (0.9 / 2.x / missing field / duplicate cases). `inject`: test hooks such as `{"correlation": "timeout"}`. Both are used in Phase 3–4; Phase 2 only needs to pass them through.
- At least 5 samples per metric per target inside the 5-minute window, so windows and counter deltas work.

**D2. Agent interface** (`netcoop/agents/base.py`, Lead): `IncidentContext(incident_id, clock, observations, topology, signatures, normalization_log)` with `evidence(id)`, `by_metric(metric)`, `for_target(id)`; `BaseAgent.handle(msg, ctx) -> list[Envelope]`; messages only via `self.make(ctx, recipient, type_, payload, in_reply_to)` → IDs `<incident>-<ABBREV>-<nn>`, timestamp = `ctx.clock`. FINDINGs via `self.finding(ctx, recipient, in_reply_to, hypothesis=..., stance=..., evidence_ids=..., reason=..., consumed_message_ids=...)` → `F-<ABBREV>-<nn>`; it refuses evidence IDs not in the incident. `ctx.threshold(metric)` reads the YAML. Abbrevs: PERF, INF, CONN, CORR, REP, ORCH. Agents import only `netcoop.protocol`, `netcoop.agents.base` and config — never each other.

**D3. Pipeline** (`netcoop/orchestrator/run.py`, Lead): normalize → TASK Performance + TASK Infrastructure → their FINDINGs through `MessageBus` → TASK Connectivity with `peer_message_ids` sent as a `Bundle` over HTTP → Connectivity FINDING(s) → (Phase 3: Correlation) → report. `--control` sends the Connectivity TASK **without** peer findings. `--local` calls Connectivity logic in-process (tests only). Every run writes `reports/<incident>.json` and `traces/<incident>.jsonl` (all messages + the HTTP request/response bodies).

**D5. Plug-in contracts** (pipeline is on `main`; `netcoop run` names any part that is still missing):
| Owner | Provide exactly | Returns |
|---|---|---|
| B | `netcoop/normalizer/normalize.py` → `normalize(fixture: dict, topology: dict, signatures: dict)` | `NormalizationResult(observations, normalization_log, validation_errors, window_end)` from `netcoop/normalizer/result.py` |
| B | `netcoop/agents/performance.py` → `class PerformanceAgent(BaseAgent)` (`agent_id="performance_agent"`, `abbrev="PERF"`) | `handle(task, ctx) -> list[Envelope]` |
| C | `netcoop/agents/infrastructure.py` → `class InfrastructureAgent(BaseAgent)` (`"infrastructure_agent"`, `"INF"`) | same |
| C | `connectivity_service/logic.py` → `handle_bundle(messages: list[Envelope], observations: list[Observation], topology: dict)`; `messages[0]` is the TASK, the rest are peer FINDINGs | `list[Envelope]` (abbrev `CONN`) |
| C | `netcoop/orchestrator/remote.py` → `send_bundle(bundle: Bundle, endpoint: str) -> ConnectivityReply` (`netcoop/orchestrator/transport.py`): raw reply messages as dicts, raw request/response bodies, `http_status`, `error` like `"AGENT_TIMEOUT: ..."` | never raises |

Stand-in versions of all of these live in `tests/test_pipeline.py` — copy their shape.

**D4. Evidence IDs** are deterministic: `EV-<nnn>` in order of (target ID, metric, timestamp). Same fixture → same IDs → same message IDs.

---

## A — Lead (Sayan): agent base + pipeline + CLI  · critical path
- [ ] **A1 (push first, within 30 min):** `netcoop/agents/base.py` per D2, with a test. This unblocks B and C.
- [ ] **A2:** `netcoop/orchestrator/run.py`: `run_incident(fixture, topology, signatures, connectivity="remote"|"local", control=False) -> RunResult(report, messages, wire)` per D3. Never raises: bad input → `invalid_input` report; Connectivity unreachable → `insufficient_evidence` with limitation `connectivity_unreachable`.
- [ ] **A3:** stub Correlation + Reporting: collect FINDINGs, emit a schema-valid `insufficient_evidence` report listing `agent_findings`, `evidence_sources`, `normalization_log` and limitation `correlation not implemented (Phase 3)`.
- [ ] **A4:** CLI `netcoop run <scenario> [--local] [--control] [--endpoint URL]`; writes report + trace; prints the report JSON.
- [ ] Tests: `tests/test_pipeline.py` — end-to-end on a fixture with `--local`; report validates; trace contains a Connectivity request whose Bundle carries the peer FINDINGs; `--control` changes Connectivity's FINDING; two runs identical apart from wall-clock.

## B — Gaurav: thresholds + normalizer + Performance agent
- [ ] **B1 (first):** fill every `null` in `signatures-1.0.yaml` (`warn`, `critical`, `rationale` with a source or reasoning). Bump nothing else. Test: no `null` left in `thresholds`.
- [ ] **B2:** `netcoop/normalizer/normalize.py`: fixture/generator → full `Observation`s per D1/D4: `alias_mapped`, `unit_converted` (+ `normalization_steps`), `missing` (value `null`, never 0), `stale` (outside the window), `malformed`; `utilization` derived from throughput/capacity and cross-checked; returns `(observations, normalization_log)`. Unknown raw field or unreadable file → an `invalid_input` result with `validation_errors`, **not** a `ValueError`.
- [ ] **B3:** `netcoop/normalizer/windows.py`: `median`, `p95`, `sustained(values, threshold, min_consecutive)`, `counter_deltas(values) -> (deltas, reset_indices)` with negative delta = reset.
- [ ] **B4:** `netcoop/agents/performance.py`: latency/jitter/loss/utilization/throughput vs capacity against the YAML thresholds → FINDING `supports` for congestion when sustained utilization + latency or drops; `contradicts` when utilization is clearly normal; cites real evidence IDs.
- [ ] Tests: normalizer (alias, unit, missing ≠ 0, stale, counter reset, invalid input), windows, Performance on congestion and normal fixtures.

## C — Amit: Infrastructure agent + Connectivity over Bundle
- [ ] **C1:** `netcoop/agents/infrastructure.py`: CPU/memory, queue-drop and interface-error **deltas**, flaps, oper status, RSSI (grey zone −75…−67 = uncertain), retries → FINDINGs for interface_fault, device_overload, hardware_failure, weak_wifi; must emit **`contradicts`** when evidence argues against a rival (e.g. errors rising while utilization is normal → contradicts congestion).
- [ ] **C2:** `connectivity_service/app.py`: `POST /messages` accepts a **Bundle** (`schemas/bundle-1.0.json`, format checker on); keep the same typed ERROR behaviour; response is a Bundle.
- [ ] **C3:** `connectivity_service/logic.py`: `handle_bundle(messages, observations, topology)` → blast radius from topology, common upstream (e.g. all affected devices behind `HOSTEL_UPLINK_01`), DNS/DHCP/reachability findings; reads peer FINDINGs and lists them in `consumed_message_ids`; its conclusion must differ when peers are absent.
- [ ] **C4:** `netcoop/orchestrator/remote.py`: HTTP client (`httpx`, timeout, 1 retry) returning the reply Bundle plus raw request/response bodies for the trace.
- [ ] Tests: Bundle valid/invalid (the REST-card = `cards.py` test already exists in `tests/test_connectivity_service.py`); peer-consumption changes output; Infrastructure emits a `contradicts`.

## D — UI/demo + fixtures (member D)
- [ ] **D1 (start now):** `scenarios/dev/` fixtures per D1 for the first six labels: `normal`, `congestion`, `interface_fault`, `weak_wifi`, `dns_failure`, `ambiguous_congestion_errors`. Values must be realistic and chosen **before** thresholds are known — don't tune to pass. Test: every fixture loads through the adapter; every ID is in the topology.
- [ ] **D2:** remaining 11 fixtures (missing/stale, unit mismatch, counter reset, 0.9, 2.x, missing field, duplicate, timeout, malformed, false report, DHCP).
- [ ] **D3:** CI job in `.github/workflows/ci.yml`: `cd dashboard && npm ci && npm run gen:types && git diff --exit-code && npm run build`.

---

## Phase 2 acceptance criteria (what AI 3 will check)
1. `signatures-1.0.yaml` has no `null` threshold; each has a rationale.
2. Normalizer: deterministic evidence IDs; alias and unit conversions recorded; missing → `null` + `missing` (never 0); stale flagged; counter reset flagged, not counted as errors; bad input → `invalid_input` report, no exception.
3. Performance, Infrastructure and Connectivity emit schema-valid FINDINGs with `stance` and ≥ 1 evidence ID that exists in the incident; Infrastructure emits ≥ 1 `contradicts` on `ambiguous_congestion_errors`.
4. Connectivity runs as its own `uvicorn` process and accepts a Bundle; its FINDING lists `consumed_message_ids`; on `congestion.json` the normal run and the `--control` run give different Connectivity conclusions. The trace holds the real HTTP request and response bodies.
5. `netcoop run scenarios/dev/<x>.json` returns a schema-valid report for all six D1 fixtures (status may be `insufficient_evidence` until Phase 3).
6. Two runs of the same fixture are identical apart from wall-clock fields.
7. REST card equals `cards.py` (existing test stays green); dashboard CI job green; `pytest -q` green on `main`.

## Exit gate — bring to PM
`main` SHA · fresh-clone `pytest -q` · `netcoop run` output for the six fixtures (normal and `--control` for congestion) · trace excerpt showing the Bundle request/response · `git diff` of the YAML thresholds.
