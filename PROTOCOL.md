# NetCoop protocol `netcoop/1.0`

The typed models in [`netcoop/protocol/models.py`](netcoop/protocol/models.py) are the single source of truth. The JSON Schemas in [`schemas/`](schemas/) and the field reference at the bottom of this file are **generated** from them by `netcoop export-schemas`. CI fails if the checked-in schemas drift from the models.

## Transport
- **In-process agents** (Performance, Infrastructure, Correlation, Reporting) exchange `Envelope` objects through the orchestrator's `MessageBus`, which validates every message before delivering it.
- **Connectivity** runs as a separate process. The orchestrator sends `POST <endpoint>/messages` with a `Bundle` (`schemas/bundle-1.0.json`): `{messages: [Envelope...], observations: [Observation...], topology: {...}}`. The response uses the same shape. The service validates against the **exported JSON Schema**, not the Python models.
- The orchestrator logs both the HTTP request and response bodies to the incident trace as wire proof.

## Message types
| type | Purpose | Key payload fields |
|---|---|---|
| `TASK` | orchestrator → agent work request | `task_id`, `capability`, `scope_ids`, `evidence_ids`, `peer_message_ids` |
| `FINDING` | agent claim with evidence | `finding_id`, `hypothesis` (`cause@id`), `stance` (`supports`/`contradicts`), `target_hypothesis`, `evidence_ids` (≥1), `reason`, `consumed_message_ids` |
| `QUERY` | Correlation asks for a targeted check | `query_id`, `question`, `target_id`, `desired_measurement` |
| `RESPONSE` | answer citing new evidence | `query_id`, `evidence_ids`, `answer` |
| `DECISION` | Correlation's ranked outcome | `decision_id`, `status`, `ranking[]`, `limitations` |
| `ERROR` | typed failure, never a silent drop | `code`, `detail`, `field_path`, `offending_message_id` |

`in_reply_to` is `null` only for a new incident or an unsolicited finding.

## Versioning and compatibility
| Incoming | Behaviour |
|---|---|
| `netcoop/1.0` | validated as-is |
| `netcoop/1.x` | accepted; unknown top-level fields moved into `extra`, with a conversion record |
| `netcoop/0.9` | upgraded to 1.0 by `protocol/compat.py`, with a visible conversion record *(TODO: Lead)* |
| `netcoop/2.x` / unknown | `ERROR` with `SCHEMA_VERSION_UNSUPPORTED` |
| missing required field | `ERROR` with `MISSING_FIELD` and `field_path` |
| duplicate `message_id` | ignored, logged, `ERROR` with `DUPLICATE_MESSAGE` |

## Determinism
Message IDs follow `<incident>-<AGENT_ABBREV>-<seq>` (for example `INC-0001-PERF-01`), and evidence IDs follow `EV-<seq>`, both deterministic for a fixed fixture. Envelope timestamps use the incident clock (the window end), not wall time. Wall-clock receive times are kept only in the SQLite log and are excluded from exported traces.

## Field reference
<!-- BEGIN GENERATED FIELD REFERENCE -->

_Generated from `netcoop/protocol/models.py` (netcoop/1.0). Do not edit by hand._

### Envelope

| Field | Type | Required | Description |
|---|---|---|---|
| `protocol` | string | yes | Protocol and version |
| `message_id` | string | yes | Deterministic, unique per incident |
| `incident_id` | string | yes |  |
| `in_reply_to` | string / null | yes | null only for a new incident or an unsolicited finding |
| `sender` | string | yes |  |
| `recipient` | string | yes |  |
| `type` | MessageType | yes |  |
| `timestamp` | string | yes | RFC 3339 date-time with timezone, UTC preferred |
| `payload` | object | yes |  |
| `extra` | object | no | Unknown optional fields preserved from compatible 1.x senders |

### TaskPayload

| Field | Type | Required | Description |
|---|---|---|---|
| `task_id` | string | yes | Deterministic task ID, e.g. INC-0001-T-PERF-01 |
| `capability` | string | yes | Capability requested, must match an agent card |
| `scope_ids` | array | no | Device/link IDs in scope |
| `evidence_ids` | array | no | Evidence the agent may read |
| `peer_message_ids` | array | no | Peer FINDING message IDs forwarded for interpretation |
| `instructions` | string | no |  |

### FindingPayload

| Field | Type | Required | Description |
|---|---|---|---|
| `finding_id` | string | yes | e.g. F-PERF-01 |
| `hypothesis` | string | yes | <cause>@<device_or_link_id> |
| `stance` | Stance | yes |  |
| `target_hypothesis` | string | yes |  |
| `evidence_ids` | array | yes | Canonical evidence IDs cited |
| `reason` | string | yes |  |
| `score` | number / null | no | Agent-local signal fit [0,1] |
| `consumed_message_ids` | array | no | Peer messages interpreted to produce this finding |

### QueryPayload

| Field | Type | Required | Description |
|---|---|---|---|
| `query_id` | string | yes |  |
| `question` | string | yes |  |
| `target_id` | string | yes | Device or link ID to check |
| `desired_measurement` | string | yes | Canonical metric or service check name |
| `related_hypotheses` | array | no |  |

### ResponsePayload

| Field | Type | Required | Description |
|---|---|---|---|
| `query_id` | string / null | no | Query answered, if any |
| `evidence_ids` | array | no |  |
| `answer` | string | no |  |

### DecisionPayload

| Field | Type | Required | Description |
|---|---|---|---|
| `decision_id` | string | yes |  |
| `status` | Status | yes |  |
| `ranking` | array | no |  |
| `limitations` | array | no |  |

### ErrorPayload

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | ErrorCode | yes |  |
| `detail` | string | yes |  |
| `field_path` | string / null | no |  |
| `offending_message_id` | string / null | no |  |

### RankedCause

| Field | Type | Required | Description |
|---|---|---|---|
| `hypothesis` | string | yes |  |
| `confidence` | number | yes |  |
| `coverage` | number | yes |  |
| `supporting_finding_ids` | array | no |  |
| `contradicting_finding_ids` | array | no |  |

### Observation

| Field | Type | Required | Description |
|---|---|---|---|
| `evidence_id` | string | yes | e.g. EV-014, deterministic per fixture |
| `source_id` | string | yes | Raw sensor/probe identity, e.g. switch-telemetry |
| `observed_at` | string | yes |  |
| `device_id` | string / null | no |  |
| `link_id` | string / null | no |  |
| `metric` | string | yes | Canonical metric name, see PROTOCOL.md |
| `value` | number / string / boolean / null | yes | null when missing/malformed — never 0 |
| `unit` | string | yes |  |
| `original_field` | string | yes |  |
| `original_value` |  | no |  |
| `normalization_steps` | array | no |  |
| `quality_flags` | array | no |  |

### EvidenceItem

| Field | Type | Required | Description |
|---|---|---|---|
| `evidence_id` | string | yes |  |
| `metric` | string | yes |  |
| `value` | number / string / boolean / null | yes |  |
| `unit` | string | yes |  |
| `source_id` | string | yes |  |

### Report

| Field | Type | Required | Description |
|---|---|---|---|
| `incident_id` | string | yes |  |
| `status` | Status | yes |  |
| `suspected_root_cause` | string / null | yes |  |
| `confidence` | number / null | yes |  |
| `confidence_method` | string | no |  |
| `signature_version` | string | yes |  |
| `affected_devices` | array | no |  |
| `affected_segments` | array | no |  |
| `supporting_evidence` | array | no |  |
| `agent_findings` | array | no |  |
| `alternative_causes` | array | no |  |
| `recommended_actions` | array | no |  |
| `evidence_sources` | array | no |  |
| `limitations` | array | no |  |
| `normalization_log` | array | no |  |
| `validation_errors` | array | no |  |
| `agent_trace_id` | string | yes |  |

**MessageType:** `TASK`, `FINDING`, `QUERY`, `RESPONSE`, `DECISION`, `ERROR`

**Stance:** `supports`, `contradicts`

**Status:** `diagnosed`, `probable`, `healthy`, `insufficient_evidence`, `invalid_input`, `failed`

**Cause:** `healthy`, `link_congestion`, `weak_wifi`, `interface_fault`, `device_overload`, `dns_failure`, `dhcp_failure`, `hardware_failure`, `upstream_dependency`

**ErrorCode:** `SCHEMA_VERSION_UNSUPPORTED`, `MISSING_FIELD`, `INVALID_VALUE`, `MALFORMED_JSON`, `DUPLICATE_MESSAGE`, `UNKNOWN_EVIDENCE`, `UNKNOWN_RECIPIENT`, `AGENT_TIMEOUT`, `AGENT_FAILURE`

**QualityFlag:** `ok`, `missing`, `stale`, `malformed`, `counter_reset`, `unit_converted`, `derived`, `alias_mapped`, `out_of_window`

<!-- END GENERATED FIELD REFERENCE -->
