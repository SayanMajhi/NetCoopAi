# Phase 1 (Milestone M1) — Data contract ready

**Window:** T0 → T+3h. **Repo:** https://github.com/SayanMajhi/NetCoopAi
**Rule:** no agent diagnostic logic, no UI features. Only: raw data, field mapping, the frozen `netcoop/1.0` contract, agent cards, topology, a bus + REST service that validate.
**Starting point:** the repo has only the foundation (`pyproject.toml`, empty packages, `netcoop --version`, `tests/test_smoke.py`). **Every file below is created from scratch.**

> **Note for your AI assistant:** `CLAUDE.md` has a section "Skeleton conventions (already implemented)". In this repo those are **not implemented yet** — treat them as the conventions to build. Paste `CLAUDE.md`, then this note, then your section as "MY TASK RIGHT NOW".

## Step 0 — everyone (10 min)
```bash
git clone https://github.com/SayanMajhi/NetCoopAi.git && cd NetCoopAi
python -m venv .venv && .venv\Scripts\activate        # mac/linux: source .venv/bin/activate
pip install -e ".[dev]" && pytest -q                  # expect: 2 passed
git checkout -b m1/<yourname>-<topic>
```
Workflow: one branch per task, PR + 1 review, merge within ~2h, `pytest -q` green on `main`. **Only A edits `netcoop/protocol/models.py`.**

## Dependency order
```
A1 Envelope + enums (T+1h) ──► C2 bus/eventlog, C3 REST service, D3 dashboard types
B2 field mapping (T+1.5h) ───► A3 metric names frozen ──► A5 tag contract-1.0 (T+3h)
B3 device IDs ───────────────► C1 topology IDs
```
A and B start at once with no dependency. C starts with topology; D starts with labels and demo script.

---

## A — Lead: protocol (critical path)
- [ ] **A1 (push by T+1h, even if partial):** `netcoop/protocol/models.py` — Pydantic v2, `extra="forbid"`:
  - enums `MessageType` (TASK, FINDING, QUERY, RESPONSE, DECISION, ERROR), `Stance` (supports, contradicts), `Status` (diagnosed, probable, healthy, insufficient_evidence, invalid_input, failed), `Cause`, `ErrorCode` (incl. `SCHEMA_VERSION_UNSUPPORTED, MISSING_FIELD, INVALID_VALUE, MALFORMED_JSON, DUPLICATE_MESSAGE, UNKNOWN_EVIDENCE, UNKNOWN_RECIPIENT, AGENT_TIMEOUT, AGENT_FAILURE`), `QualityFlag`.
  - `Envelope` (`protocol, message_id, incident_id, in_reply_to, sender, recipient, type, timestamp, payload, extra`) with a validator that `payload` matches `type`.
- [ ] **A2:** payload models `TaskPayload, FindingPayload` (`finding_id, hypothesis` = `cause@id`, `stance, target_hypothesis, evidence_ids` min 1, `reason, score, consumed_message_ids`), `QueryPayload, ResponsePayload, DecisionPayload, RankedCause, ErrorPayload`; `Observation`; `Bundle` (`messages, observations, topology`); `EvidenceItem`; `Report` with status rules (only diagnosed/probable name a cause; invalid_input/failed have `confidence=null`).
- [ ] **A3:** `netcoop/protocol/export.py` + CLI command `netcoop export-schemas` → writes `schemas/{message,finding,report,observation,bundle}-1.0.json` and the generated field table in `PROTOCOL.md`.
- [ ] **A4:** `netcoop/protocol/compat.py`: 1.x accepted with unknown fields moved to `extra`; 2.x → `SCHEMA_VERSION_UNSUPPORTED`; 0.9 → stub that fails clearly (upgrade is Phase 3). `netcoop/protocol/cards.py`: 5 agent cards (`agent_id, endpoint, protocol_versions, owned_metrics, hypotheses`).
- [ ] **A5:** `examples/`: one valid file per message type, `finding_v0_9.json`, `message_v2_0.json`, `report_diagnosed.json`, `report_insufficient.json`, `report_invalid_input.json`. After B's mapping: align metric names, then `git tag contract-1.0`.
- [ ] Tests: `tests/test_protocol.py` (examples validate; FINDING without `stance` error names the field; FINDING with 0 evidence fails; unknown cause fails; v2 rejected; v0.9 fails plain 1.0; 1.x extras preserved; report status rules) and `tests/test_schema_drift.py` (regenerate to temp dir, fail on diff).

**Accept:** `pytest tests/test_protocol.py tests/test_schema_drift.py` green; `netcoop export-schemas` leaves `git status` clean; tag `contract-1.0` exists.

## B — Generator + signatures
- [ ] **B1 first, before anything:** run the organizer Colab generator **unmodified** (link in `ARCHITECTURE.md` §3). Save output to `data/generator_raw/` (every scenario type it offers). Write `data/generator_raw/RETRIEVED.md`: date, notebook URL, revision/copy, parameters, seeds, whether labels exist.
- [ ] **B2 (post in team chat by T+1.5h):** `data/field_mapping.md` table: raw key | example value | unit | interval | null behaviour | canonical metric | conversion | mapped/rejected + reason. Plus topology representation, timestamp format/timezone, counter vs rate.
- [ ] **B3:** send the list of device/link IDs found in the generator output to C.
- [ ] **B4:** `netcoop/config/signatures-1.0.yaml`: `version: "1.0"`, per-metric `warn/critical/direction/rationale` (values may stay `null` until Phase 2), window (5 min, ≥3 consecutive), RSSI grey zone (-75..-67), the signatures (congestion, weak Wi-Fi, interface fault, DNS, DHCP) with required/corroborating/contradicting checks, healthy-coverage rule, decision gates.
- [ ] **B5:** `netcoop/normalizer/adapter_generator.py`: `load_raw(path)` → list of dicts with `original_field/original_value` preserved. Test `tests/test_adapter.py` runs it on the untouched raw file.

**Accept:** every raw key appears in the mapping; YAML loads with `version: "1.0"`; adapter runs on the raw file without crashing. **If the generator is inaccessible or unlabeled, say so in chat by T+1.5h** — fallback is hand-labeled scenarios, stated in README.

## C — Topology + bus + REST
- [ ] **C1 (start now):** `scenarios/topology.json`: nodes (`id, type, zone`), links (`link_id, a, b, capacity_mbps`); 10–14 devices: core router, hostel + department distribution switches, access switches, ≥2 APs, DNS/DHCP server; **one shared upstream** for blast radius. Use B's IDs when available.
- [ ] **C2 (after A1):** `netcoop/orchestrator/eventlog.py`: SQLite table (`message_id, incident_id, sender, recipient, type, protocol, in_reply_to, valid, error, body_json, received_at`), `record()`, `rows()`. `netcoop/orchestrator/bus.py`: `MessageBus.deliver(raw) -> (envelope | None, error_envelope | None)` — validates, drops duplicate `message_id` (logged), turns every failure into a typed ERROR envelope; logs everything.
- [ ] **C3 (after A3 schemas exist):** `connectivity_service/app.py` (FastAPI): `GET /card`, `POST /messages` validates the body with `jsonschema` against `schemas/message-1.0.json` (not the Python models); valid → stub RESPONSE with correct `in_reply_to`; invalid → ERROR with field path + code. Runs alone: `uvicorn connectivity_service.app:app --port 8001`.
- [ ] Tests: `tests/test_bus.py` (valid, duplicate, invalid all logged; v2 → unsupported), `tests/test_connectivity_service.py` (card; valid → reply; missing field → typed ERROR; v2 → unsupported).

**Accept:** `docs/m1_curl_proof.md` with real curl output: valid → 200 RESPONSE; missing `sender` → typed ERROR; duplicate ID → logged + ignored; the 3 SQLite rows shown.

## D — Labels + demo + dashboard
- [ ] **D1 (start now):** `scenarios/labels.json` — expected `status`, `cause`, `affected_ids` for all 16 dev scenarios: normal, false report, congestion, weak Wi-Fi, interface fault, DNS, DHCP, ambiguous congestion+errors, missing/stale, unit mismatch, counter reset, schema 0.9, unsupported 2.x, missing message field, duplicate message, correlation timeout. Must use C's topology IDs. Never read by runtime code.
- [ ] **D2 (start now):** `docs/DEMO_SCRIPT.md` — the `ARCHITECTURE.md` §7 demo sequence, timed to 4 min, with the command for each beat.
- [ ] **D3:** `dashboard/` React + Vite + TS (`npm create vite@latest dashboard -- --template react-ts`); `npm run gen:types` generating TS types from `schemas/*.json` with `json-schema-to-typescript` (after A3). Mock views reading `examples/`: event feed, competing causes, evidence IDs, status badge (all 6), raw JSON panel.
- [ ] **D4:** `README.md` final sections: 3-command setup, run/test commands, limitations placeholder.

**Accept:** `labels.json` has no `TODO` and all 16 classes; `npm run dev` renders `examples/report_diagnosed.json` (screenshot); types regenerate from schemas.

---

## M1 exit gate — bring to PM, then AI 3 review
1. `main` commit hash after all merges + `pytest -q` output.
2. `models.py`, `schemas/`, `PROTOCOL.md`, tag `contract-1.0`.
3. `data/generator_raw/` + `RETRIEVED.md` + `field_mapping.md`.
4. `docs/m1_curl_proof.md`.
5. `labels.json` + dashboard screenshot.
