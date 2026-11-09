# NetCoop AI architecture

This document describes the system as built. Field-level message definitions are in [PROTOCOL.md](PROTOCOL.md); the Connectivity process is documented in [docs/CONNECTIVITY_SERVICE.md](docs/CONNECTIVITY_SERVICE.md). The original plan, including features that were not built, is kept in [docs/history/DESIGN_PLAN.md](docs/history/DESIGN_PLAN.md).

All agents are deterministic and rule-based. There is no LLM or trained model in the diagnostic path. Telemetry is synthetic; nothing touches a real network.

## 1. Components

Workflow: **Telemetry → Normalization → Specialist Analysis → Agent Communication → Correlation and Verification → Diagnosis → Evidence-Based Report**

| Component | Code | Owns | Produces |
|---|---|---|---|
| Normalizer (service) | `netcoop/normalizer/` | Aliases, unit conversion, time windows, counter resets, utilization cross-check, quality flags | Canonical `Observation`s with provenance |
| Orchestrator and bus (service) | `netcoop/orchestrator/` | Task routing by agent card, per-message validation, deadlines, retries, event log | TASKs, typed ERRORs, the incident trace |
| Performance agent | `netcoop/agents/performance.py` | Latency, jitter, loss, throughput, utilization | FINDINGs on congestion |
| Infrastructure agent | `netcoop/agents/infrastructure.py` | CPU, memory, queue drops, interface errors, flaps, oper status, Wi-Fi signal and retries | FINDINGs that support **and contradict** causes |
| Connectivity agent (separate process) | `connectivity_service/` | Topology, blast radius, shared upstream, DNS, DHCP, reachability | FINDINGs built from its own checks plus the peer FINDINGs it was sent |
| Correlation agent | `netcoop/agents/correlation.py` | Signature checks, ranking, QUERY rounds, gates, abstention | QUERYs and one DECISION |
| Reporting agent | `netcoop/agents/reporting.py` | Evidence integrity, confidence caps, recommended actions | `report-1.0` JSON and Markdown |

Agents exchange conclusions only as validated messages. Two imports cross agent modules at code level: Performance reuses a polling-interval helper from `correlation.py`, and Reporting reads Correlation's decision structure, since it serializes that decision.

**What "separate process" means here.** Connectivity is launched with `uvicorn` and reached over HTTP. It validates every body with `jsonschema` against the exported `schemas/*.json`, then with the typed models. It lives in the same repository and imports the shared protocol models, so it is an independent runtime, not an independently developed codebase. Any client that speaks the exported schema can call it; [`examples/wire/`](examples/wire/) has two bodies you can send with `curl`.

## 2. Protocol

`netcoop/protocol/models.py` is the single executable source of truth. `netcoop export-schemas` generates `schemas/*.json` and the field reference in `PROTOCOL.md`; `npm run gen:types` generates the dashboard types. Tests and CI fail on drift.

Every message is an envelope carrying `protocol`, `message_id`, `incident_id`, `in_reply_to`, `sender`, `recipient`, `type`, `timestamp` and a typed payload. Types are `TASK`, `FINDING`, `QUERY`, `RESPONSE`, `DECISION` and `ERROR`.

The bus validates every message before delivery and never drops one silently. It accepts `netcoop/1.x` and moves unknown fields into `extra`, upgrades the `netcoop/0.9` FINDING with a recorded conversion, and answers anything else with `SCHEMA_VERSION_UNSUPPORTED`. A FINDING citing evidence that is not in the incident gets `UNKNOWN_EVIDENCE` and is excluded.

Message and evidence IDs are deterministic for a fixed input, and envelopes use the incident clock, so reruns produce the same trace. The exception is ERROR envelopes created by the Connectivity service, which carry wall-clock time.

## 3. Telemetry and fault signatures

Every observation keeps `evidence_id`, `source_id`, `observed_at`, target, canonical metric, value, unit, original field and value, normalization steps and quality flags. A missing value stays `null` and is flagged; it is never turned into zero.

- **Aliases and units:** `rtt_sec`, `ping_ms`, `latency_s`, `throughput_kbps`, `crc_errors` and others map to canonical names, and each conversion is logged.
- **Counters:** `queue_drops`, `interface_errors` and `link_flap_count` are cumulative and judged per interval. A negative delta is flagged as `counter_reset` and is never evidence.
- **Window:** five minutes ending at `window_end`. Older samples are flagged `stale` and `out_of_window`.
- **Utilization cross-check:** measured utilization is compared with throughput over capacity. It is marked `malformed` only when the two disagree on the verdict.
- **One bad item does not sink an incident.** Unknown metrics and unusable records are skipped and named in `validation_errors`. Only an input with nothing usable left is `invalid_input`. Skipped items also block a `healthy` verdict.

Signatures, thresholds and gates live in [`netcoop/config/signatures-1.0.yaml`](netcoop/config/signatures-1.0.yaml), each threshold with its rationale. Scenario files contain no expected outputs, and `scenarios/labels.json` is read only for scoring.

| Cause | Required | Corroboration |
|---|---|---|
| `link_congestion` | Utilization above critical for 3 samples in one run | Queue drops rising, or latency above warn |
| `interface_fault` | Errors or flaps rising, and downstream loss above warn | Interface down or flapping (optional) |
| `weak_wifi` | RSSI weak or in the -75 to -67 dBm grey zone, and retries above warn | Loss, or a healthy AP uplink (optional). A lossy or saturated uplink refutes it |
| `dns_failure`, `dhcp_failure` | Service check failing on clients, and the server reachable | Several clients failing (optional) |
| `hardware_failure` | Interface down or flapping, or device unreachable | Optional; refuted if the device answers |
| `device_overload` | CPU or memory above critical, sustained | Latency or queue drops |

## 4. Investigation and decision

1. **Normalize.** Produce evidence, or an `invalid_input` report with field-level errors.
2. **First pass.** The orchestrator sends TASKs to Performance and Infrastructure, whose FINDINGs pass through the bus.
3. **Peer interpretation.** The orchestrator POSTs a Bundle to Connectivity: its TASK, the peer FINDINGs, the observations and the topology. Connectivity adds blast radius and scope for each fault a peer named, using only its own checks, and lists the peer messages in `consumed_message_ids`. With `--control` it receives no peers and cannot produce those findings.
4. **Rank.** Correlation nominates candidates from FINDINGs, then re-evaluates each signature itself on the raw evidence. It does not trust an agent's verdict, and a measurement cited by several agents counts once.
5. **Verify.** Round 1 sends a QUERY about the leader's key measurement to the agent that owns it. Round 2 queries a runner-up within 0.15. There are at most two rounds per attempt. The agent answers with on-demand probe samples when the scenario has them (new evidence IDs); otherwise it re-reads the window and says that no new probe was run. DNS and DHCP are not queried, because their evidence is already Connectivity's pass or fail checks.
6. **Decide.** Apply the gates below, or abstain.

**Confidence is `heuristic-v1`, a documented score and not a calibrated probability:**

```text
raw = clamp(0.40*signal_fit + 0.25*topology_fit + 0.20*time_fit
          + 0.15*independent_source_fit - 0.15*contradiction_strength, 0, 1)
confidence = round(raw * coverage, 2)
```

Each factor is defined in the docstring of `netcoop/agents/correlation.py`. `topology_fit` is where Connectivity's scope finding enters: 1 when Connectivity scoped the hypothesis, 0.5 when the target is only known to the topology. `independent_source_fit` counts raw sensors, not agents.

| Status | Gate | Reported confidence |
|---|---|---|
| `diagnosed` | All required checks pass, corroboration met, confidence at least 0.70, coverage at least 0.75, two independent raw sources, lead at least 0.15, no unresolved rival, Connectivity answered | at most 0.95 |
| `probable` | Corroborated, not refuted, confidence at least 0.50; the reason it is not diagnosed is named | at most 0.69 |
| `healthy` | Fresh normal measurements, passing service and path checks, no missing or stale data in the area in question, no skipped input, Connectivity answered | at most 0.95 |
| `insufficient_evidence` | Otherwise; no cause named | at most 0.49 |
| `invalid_input`, `failed` | Nothing usable, or an internal failure | `null` |

The uncapped score stays in the DECISION ranking, and any cap is named in `limitations`.

## 5. Report

All outcomes use one `report-1.0` envelope: `status`, `suspected_root_cause` (`null` unless diagnosed or probable), `summary`, `confidence`, `confidence_method`, `affected_devices`, `affected_segments`, `supporting_evidence[]`, `agent_findings`, `alternative_causes`, `recommended_actions`, `evidence_sources`, `limitations`, `normalization_log`, `validation_errors` and `agent_trace_id`.

Reporting cites only evidence the incident contains and only IDs in the topology, and it excludes stale or malformed readings. Markdown and the dashboard render this JSON and add no conclusions.

Outputs are `reports/<incident>.json`, `reports/<incident>.md` and `traces/<incident>.jsonl`. The trace holds every message plus the raw HTTP request and response bodies. A control run writes `<incident>--control.*`.

## 6. Failure handling

`run_incident` always returns a schema-valid report.

| Failure | Result |
|---|---|
| Unknown protocol version, missing field, bad value, malformed JSON, duplicate ID | Typed ERROR with `field_path`; listed in the report's `validation_errors`; the run continues |
| Bad telemetry item | Skipped and named; the rest is diagnosed |
| Performance or Infrastructure hangs or crashes | `AGENT_TIMEOUT` after 5 s or `AGENT_FAILURE`, plus a limitation |
| A QUERY responder hangs or crashes | Typed ERROR; that check stays unverified and the decision still ships |
| Connectivity unreachable, failing, or replying with no valid message | Typed ERROR. The run cannot end in `diagnosed` or `healthy`. Connection errors, timeouts and 502/503/504 are retried once; a 4xx or 500 is the agent's typed answer and is not |
| Correlation raises or reports a timeout | One retry, then `insufficient_evidence` with `correlation_failed` and no invented score. The 5 s deadline covers Performance, Infrastructure and QUERY responders, not Correlation itself or in-process Connectivity |
| Reporting fails | `failed` report; the trace is kept |

## 7. Evaluation

`scenarios/dev/` holds 17 labelled regression fixtures. They were available during development and are not evidence of accuracy.

`netcoop eval` generates held-out incidents from `netcoop/evaluation/heldout.py`, which imports no agent, decision code or YAML. Labels come from the fault the generator overlays. It reports class counts, a confusion matrix, root-cause accuracy, accuracy on named causes, affected-device accuracy, healthy false-diagnosis rate, abstention by class, evidence-link integrity and protocol counts. An abstention is counted separately from a wrong cause.

Results, methodology and limits: [docs/results/EVALUATION.md](docs/results/EVALUATION.md).

## 8. Design decisions

- **Correlation re-checks signatures instead of voting.** Agents nominate; evidence decides. Agreement between agents adds nothing, because three agents citing one sensor are still one source.
- **Abstention is a first-class outcome.** Each gate has a named reason, which appears in `limitations`.
- **One agent over HTTP from the start.** It forces the protocol to be a real wire contract, and it is why the Bundle shape carries evidence and topology explicitly.
- **Connectivity never re-cites a peer's evidence.** It references peers through `consumed_message_ids` and cites only its own checks, so a measurement cannot be laundered into an apparent second source.
- **Thresholds live in versioned config.** Each has a written rationale and can be changed without touching agent code.
