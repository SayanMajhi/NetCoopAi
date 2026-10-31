# NetCoop AI — competition architecture

**Mission:** Given noisy, heterogeneous campus network telemetry, five agents must exchange **validatable evidence**, challenge one another, and publish one defensible JSON diagnosis. Synthetic data or an authorized test network is sufficient. No production campus changes are part of the prototype.

**Winning emphasis:** interoperability **30%**, evidence and accuracy **25%**, orchestration and conflict resolution **15%**, robustness **15%**, usability and innovation **10%**, code/docs/demo **5%**. The first four areas total **85%**. A polished dashboard cannot rescue an unproven diagnosis or a broken agent protocol.

## 1. Architecture and ownership

<!-- Diagram added in Phase 6: ![System architecture showing validation, orchestrator, agents, evidence, and report](assets/system-architecture.png) -->

| Component | Owns | Must hand off |
| --- | --- | --- |
| **Normalizer** (service, not an agent) | Source adapters, units, time windows, counter deltas, topology IDs, validation. | Canonical records with provenance and quality flags. |
| **Orchestrator** (service) | Capability registry, task routing, message validation, timeouts, retries, bounded verification, event log. | Validated tasks/findings and one final decision request. |
| **Performance Agent** | Latency, jitter, loss, throughput versus capacity, utilization, congestion. | Hypotheses and evidence to Connectivity and Correlation. |
| **Infrastructure Agent** | Device status, CPU/memory, queue drops, interface errors, link flaps, Wi-Fi signal/retries. | Supporting **and contradictory** findings to Connectivity and Correlation. |
| **Connectivity Agent** (separate REST process) | Topology and blast radius; DNS/DHCP, reachability, common upstream dependency. | Reads Performance/Infrastructure findings, then publishes its own scope and service finding. |
| **Correlation Agent** | Rank causes, inspect contradictions, request targeted checks, abstain when necessary. | One decision with evidence IDs, alternatives, confidence, and limitations. |
| **Reporting Agent** | Validate and serialize the required JSON; explain recommended checks/actions. | JSON and human-readable view using only cited findings. |

The three diagnostic agents have separate capabilities and messages. **Connectivity consumes peer findings before its final output**, which demonstrates actual agent collaboration. One agent runs outside the main Python process over REST, so the protocol is a real wire contract rather than an internal function signature. Agent cards advertise `agent_id`, endpoint, supported protocol versions, owned metrics, and hypotheses; the orchestrator routes by these cards and flags uncovered metrics.

Use a small hybrid design: deterministic telemetry calculations and schema gates; a constrained LLM, if available, for selecting the next read-only check, comparing hypotheses, or explaining a decision. LLM output is structured and rejected if it cites nonexistent evidence. The rule path remains runnable without an API key. Do **not** present the normalizer or one large prompt as an additional agent.

## 2. One canonical protocol

This file defines the intended architecture. **The versioned typed models are the single executable source of truth** for required fields, enums, types, constraints, and units. Generate `schemas/message-1.0.json`, `schemas/finding-1.0.json`, and `schemas/report-1.0.json` from those models; generate the field reference in `PROTOCOL.md` from the same definitions and add human explanations there. CI must regenerate the schemas and fail if the checked-in copies differ. Both the Python services and the separate Connectivity process validate against the exported schemas at their receive boundaries. Any separate design pack must be reconciled to these names before implementation.

**Message envelope, for every agent and transport:**

```json
{
  "protocol": "netcoop/1.0",
  "message_id": "INC-0001-INF-02",
  "incident_id": "INC-0001",
  "in_reply_to": "INC-0001-ORCH-01",
  "sender": "infrastructure_agent",
  "recipient": "correlation_agent",
  "type": "FINDING",
  "timestamp": "2026-10-31T10:30:00Z",
  "payload": {
    "hypothesis": "link_congestion@HOSTEL_UPLINK_01",
    "stance": "contradicts",
    "target_hypothesis": "link_congestion@HOSTEL_UPLINK_01",
    "evidence_ids": ["EV-014"],
    "reason": "Interface errors rise while utilization stays below baseline"
  }
}
```

Allowed `type` values are `TASK`, `FINDING`, `QUERY`, `RESPONSE`, `DECISION`, and `ERROR`. `in_reply_to` is `null` only for a new incident or unsolicited finding. Every FINDING has a hypothesis, `stance` (`supports` or `contradicts`), target hypothesis, evidence IDs, and reason. QUERY names the unresolved question, target device/link, and desired measurement; RESPONSE cites the new evidence. The bus validates **every** message before delivery. A bad or missing field emits a typed ERROR and enters the incident trace; it never silently disappears.

**Compatibility:** `netcoop/1.x` accepts known compatible minor versions, preserves unknown optional fields in `extra`, and rejects missing required fields. A small adapter upgrades the demonstrated `netcoop/0.9` dialect to `1.0` with a visible conversion record. `2.x` returns `SCHEMA_VERSION_UNSUPPORTED`. The separate Connectivity process uses the exported JSON Schema, making non-Python integration possible. Agent cards declare supported versions before routing.

**Wire proof:** run Connectivity as a separate process and capture the actual request and response bodies in the incident trace. For each delivered message, log its schema version, sender, recipient, `message_id`, `in_reply_to`, validation result, and cited finding/evidence IDs. In the demo, show a Performance or Infrastructure FINDING arriving at Connectivity, identify the field it interpreted, and show how its final scope or service finding changed. A control run without that peer finding must produce a different Connectivity conclusion. A printed sequence of agent names alone is not proof of collaboration. Keep the raw trace as a reproducible demo artifact.

**Trace stability:** source IDs and agent-local message IDs are deterministic for a fixed fixture. Findings are sorted by stage, agent ID, and evidence ID before scoring. Wall-clock receive times are kept for operations but excluded from reproducibility snapshots. Duplicate `message_id` values are ignored and logged.

## 3. Telemetry contract and fault signatures

Inspect and run the [organizer's Colab generator](https://colab.research.google.com/drive/1xFOtO114qWICAxGisK9K6oOHoOxwvGi1#scrollTo=hPEqEB2SOZQz) **before freezing the schema**. Save one raw generated sample, its generator revision or retrieval date, and a field-mapping table in the repository. Record observed keys, units, sample interval, topology representation, null/missing behavior, and whether ground-truth labels exist. Then implement an adapter that converts the raw sample to canonical observations and records every conversion or rejection. The acceptance run must ingest an untouched generator output through this adapter. If the generator is inaccessible, state that limitation in the README and use a documented substitute; do not claim organizer-data compatibility. Do not tune rules to a handful of hand-written fixtures.

Every canonical observation carries `evidence_id`, `source_id`, `observed_at`, `device_id` or `link_id`, metric, value, unit, original field/value, normalization steps, and quality flags. Devices and links have stable IDs; each link also has `capacity_mbps`. A metric is missing, stale, or malformed explicitly—never converted to zero. The report's `normalization_log` lists conversions; `limitations` holds actual uncertainty.

| Domain | Canonical fields and units | Required handling |
| --- | --- | --- |
| Performance | `latency_ms`, `jitter_ms`, `packet_loss_pct`, `throughput_mbps`, `capacity_mbps`, `link_utilization_pct`. | Derive utilization from throughput/capacity when necessary; cross-check if both supplied. |
| Infrastructure | `cpu_pct`, `memory_pct`, `queue_drops`, `interface_errors`, `link_flap_count`, `interface_oper_status`. | Treat drops/errors as **cumulative counters** unless metadata says rate; compute deltas per time interval and flag negative deltas as resets. |
| Wireless | `rssi_dbm`, `retry_rate_pct` (aliases `retries`, `retransmissions`). | Require signal and retry evidence; `-75 < RSSI < -67 dBm` is an uncertain zone, not a normal/weak verdict. Check AP uplink health. |
| Services | DNS lookup success/failure, DHCP lease success/failure, IP reachability, affected clients. | Connectivity owns DNS/DHCP findings; distinguish service failure from general path failure. |
| Context | UTC timestamps, topology edges, link capacity, source type, baseline. | Align observations to windows; do not correlate stale or mismatched periods as if simultaneous. |

**Windowing:** use a configurable five-minute incident window. Compute median and p95 for latency/loss, sustained threshold crossing for congestion (at least three consecutive samples when sample density permits), and counter-delta rates and slopes for errors/drops. Sparse samples lower coverage. Global, versioned fault signatures and thresholds live in configuration, **not inside individual fixture expected outputs**. Record the threshold version in each report.

**Global signatures:** congestion requires high sustained utilization plus queue-drop/latency corroboration; weak Wi-Fi requires poor or grey-zone RSSI plus elevated retries and a healthy AP uplink; interface fault requires rising error rate or flaps with downstream loss; DNS/DHCP failure requires service-specific failure with relevant path checks. A healthy finding requires enough fresh coverage of the reported scope. These signatures are testable hypotheses, not claims that every network has identical baselines.

## 4. Agentic investigation and decision

<!-- Diagram added in Phase 6: ![Diagnostic workflow showing verification, abstention, JSON output, and optional review](assets/diagnostic-workflow.png) -->

1. **Normalize and validate.** Produce canonical evidence or an `invalid_input` JSON report with field-level errors.
2. **Parallel first pass.** Performance and Infrastructure inspect independent signals. Connectivity starts topology/service checks.
3. **Peer interpretation.** The orchestrator sends their FINDING messages to Connectivity; it updates the blast radius and names any shared upstream dependency. Its output cites the messages it used.
4. **Correlation.** Rank global fault signatures, inspect `supports` and `contradicts` findings, and issue a QUERY when evidence is disputed, stale, thin, or the top two causes are close. One **deliberate verification of the leading cause** is included in the demo even when the initial case is clean; the ambiguous scenario must show a QUERY that changes or weakens the ranking. Maximum two rounds.
5. **Report or abstain.** Reporting validates evidence IDs, affected IDs, output schema, and recommended actions. If Correlation fails after a retry, the orchestrator emits an `insufficient_evidence` report from the available findings and records `correlation_failed` in limitations. If no usable findings exist, use `failed`.

**One status enum across code, schema, UI, and tests:** `diagnosed`, `probable`, `healthy`, `insufficient_evidence`, `invalid_input`, `failed`. `diagnosed` and `probable` may name a likely cause. The other statuses use `suspected_root_cause: null`. `healthy` requires sufficient negative checks; absence of alerts alone is insufficient.

**One confidence method (`heuristic-v1`):**

```text
raw = clamp(0.40*signal_fit + 0.25*topology_fit
          + 0.20*time_fit + 0.15*independent_source_fit
          - 0.15*contradiction_strength, 0, 1)
confidence = round(raw * coverage, 2)
```

All factors are in `[0,1]`. `coverage` is the fraction of required signature checks that are fresh and valid. `independent_source_fit` is 1 only for at least two distinct raw sensors/probes, 0.5 for distinct measurement types from one sensor, and 0 otherwise; **agent count gives no bonus**. A raw measurement cited by three agents is counted once. Contradictions come from FINDING messages with `stance: contradicts`; deduplicate by evidence ID before calculating their strength.

**Decision gates:** `diagnosed` requires confidence >= 0.70, coverage >= 0.75, two independent raw sources, and a >= 0.15 lead over the runner-up. `probable` requires confidence >= 0.50 with a named limitation or unresolved rival. Lower coverage/score or unresolved major contradictions yield `insufficient_evidence`. Healthy requires explicit normal measurements and successful relevant service/path checks. These are documented **heuristics**, not calibrated probabilities; evaluate false positives and accuracy on data unseen during threshold choice.

## 5. Unified JSON result

All terminal outcomes produce the same report envelope. Arrays may be empty when a field is not applicable; `validation_errors` is populated for `invalid_input`. A human-readable report and dashboard render this JSON rather than inventing additional conclusions.

```json
{
  "incident_id": "INC-0001",
  "status": "diagnosed",
  "suspected_root_cause": "Likely congestion on HOSTEL_UPLINK_01",
  "confidence": 0.81,
  "confidence_method": "heuristic-v1",
  "signature_version": "1.0",
  "affected_devices": ["HOSTEL_CORE_SW_01"],
  "affected_segments": ["Hostel AP uplink"],
  "supporting_evidence": [
    {"evidence_id": "EV-011", "metric": "link_utilization_pct", "value": 94, "unit": "percent", "source_id": "switch-telemetry"},
    {"evidence_id": "EV-012", "metric": "latency_ms", "value": 280, "unit": "ms", "source_id": "client-probe"},
    {"evidence_id": "EV-013", "metric": "queue_drops_per_min", "value": 160, "unit": "packets/min", "source_id": "switch-telemetry"}
  ],
  "agent_findings": ["F-PERF-01", "F-INF-01", "F-CONN-01"],
  "alternative_causes": ["Upstream congestion"],
  "recommended_actions": ["Inspect egress queue and upstream utilization"],
  "evidence_sources": ["performance_agent", "infrastructure_agent", "connectivity_agent"],
  "limitations": ["Upstream utilization not measured"],
  "normalization_log": ["rtt_sec 0.28 -> latency_ms 280"],
  "validation_errors": [],
  "agent_trace_id": "INC-0001-TRACE"
}
```

Every cited evidence and agent-finding ID must exist; every affected ID must be in the topology. The confidence shown is illustrative, not a measured campus probability. For `invalid_input` or `failed`, confidence is `null` and validation errors or failure details explain why. For a `probable` or abstained case, explain the missing check and the next safe diagnostic action.

## 6. Priority, optional features, and proof

The brief makes MCP, HITL, persistence, dashboard, and remediation optional; it specifically encourages a dashboard. **We plan all five**, but do not let them block the scored core. Logs are core; full checkpoint/resume is not.

| Priority | Feature | Smallest credible proof | Gate |
| --- | --- | --- | --- |
| **P0** | Five-agent protocol, diagnosis, validation, JSON, tests, CLI | External Connectivity agent exchanges schema-valid messages; ambiguous QUERY changes ranking; malformed input yields ERROR/JSON. | Must work before UI or stretch work. |
| **P1** | React + Vite dashboard | Live or replayable event feed, telemetry, competing causes, evidence IDs, and final JSON. | Build after P0 end-to-end run. |
| **P2** | MCP | Read-only `get_telemetry`, `get_topology`, and probe tools expose the same fixture-backed data; one call appears in trace. | Start after the P0 REST path works. MCP is a tool interface, not the agent message protocol. |
| **P3** | Durable pause/resume | SQLite checkpoint at a task boundary; resume skips accepted message IDs. Basic SQLite event logging ships with P0. | Add if integration is green. |
| **P3** | HITL + simulated remediation | Approve/reject one suggested action; alter a fixture copy only; compare before/after and log result. | Add together after diagnosis/UI are stable. |

No production configuration commands, cloud hosting, or fine-tuning are in the critical path. If time slips, the submission still has a complete P0 diagnosis and a strong demo; optional features are shown only if actually working.

## 7. Evaluation and acceptance gates

Build a labeled scenario suite covering normal/false report, congestion, weak Wi-Fi, interface fault, DNS, DHCP, ambiguous congestion plus interface errors, missing/stale telemetry, unit mismatch, counter reset, schema 0.9 upgrade, unsupported 2.x, missing message field, duplicate message, and Correlation timeout. The sample generator, if accessible, supplies additional unseen runs. Do not use fixture labels as runtime features.

Before scoring the final build, freeze the signature thresholds and confidence gates on a development set. Reserve separate scenario seeds and topology/metric combinations as a **held-out set**; do not use those labels or outputs to change thresholds. `netcoop eval --n 50 --noise` should run at least 50 labeled, noisy held-out cases spanning every required fault class and healthy operation. Report the class counts and a confusion matrix, overall root-cause accuracy, accuracy on cases where the system named a cause, affected-device accuracy, healthy-network false-diagnosis rate, abstention rate by class, evidence-link integrity, and protocol/error counts. Count an abstention separately from a wrong named cause; do not hide either in an aggregate accuracy number. Repeat with different held-out seeds, publish all seed values, threshold/signature versions, generator revision, and results in README, and include at least one representative wrong or abstained case with its trace. If the organizer generator has no labels, use the separately labeled scenario suite for accuracy and report generator-only runs as compatibility checks. A single passing test per fault class or 50 correlated variants of one fixture is **not** accuracy evidence.

**Acceptance milestones (in dependency order):**

| Milestone | Must be demonstrably true |
| --- | --- |
| Data contract ready | Commit a raw generator sample and field mapping, demonstrate its adapter, then freeze the canonical schema, fault signatures, message/report contracts, and agent cards. Generated JSON Schemas match the typed models. |
| Interoperability proven | Two agents exchange schema-valid messages; the external REST agent receives and uses a peer finding; removing that finding changes its conclusion; the report cites two distinct raw sources. Preserve the wire trace. |
| Core diagnosis working | A complete P0 run handles normal operation, multiple faults, an ambiguous QUERY, malformed data, version mismatch, and Correlation failure fallback. |
| Results evaluated | Run the held-out 50-scenario evaluation, publish per-class results and failure examples in README, and show the trace and JSON in the React UI. Add optional features to the demo only after they pass their own checks. |
| Submission ready | The repository, setup instructions, reproducible tests and data, and demo recording are complete and reviewed together. |

**Demo sequence:** ingest an untouched generator output and show its recorded field mapping; show the 0.9-to-1.0 adapter and a 2.x ERROR; show actual Performance/Infrastructure wire messages changing Connectivity's scope, including the control run without the peer finding; run the ambiguous case and show a QUERY changing the ranking; show a healthy or insufficient-evidence report; end with JSON evidence IDs and held-out evaluation results, including the false-diagnosis and abstention rates. Show optional MCP/HITL/simulation only if their complete proof works. Save the raw trace, exact input, command, and expected output for a judge to reproduce the core run.

## 8. Team and delivery plan

| Owner | Primary responsibility | Integration contract |
| --- | --- | --- |
| Lead | Protocol, exported JSON Schemas, Correlation, score gates; then MCP slice. | Publishes schema and examples after generator inspection; accepts agent FINDING/QUERY tests. |
| Backend 1 | Normalizer, Performance, global signatures, 50-run evaluation; then fixture simulation. | Supplies canonical observations, counter/window tests, and labeled results. |
| Backend 2 | Infrastructure, Connectivity REST process, orchestrator, registry, fallback; then checkpoint/resume. | Proves peer-consumption and external message round trip. |
| UI/demo | React + Vite dashboard, trace replay, report view; then HITL and video. | Reads report/event API and exported schemas; does not redefine status or field names. |

Inspect the generator before freezing the **observed** data contract. Keep time for integration fixes and rest, and cut optional features if they threaten the next acceptance milestone. README must link this file and include setup in three commands, test/eval commands, scenario inputs, protocol/schema field meanings, thresholds, limitations, and demo instructions. Deliver the source repository, reproducible tests/data, a runnable CLI or dashboard, and a 3-5 minute video.

## 9. Why this design, not the previous one

The earlier architecture overpromised optional features, used a heuristic that could be tuned to its own fixtures, left the protocol as prose rather than a portable contract, and had no clear proof that agents independently exchanged and interpreted findings. This version spends engineering time first on the **85% of the rubric** determined by protocol, evidence, coordination, and robustness. The React UI and optional integrations enhance an already-working diagnosis.

Research and product references: [multi-agent telecom troubleshooting](https://arxiv.org/html/2511.00651v2), [NIKA](https://arxiv.org/html/2512.16381v1), [FaulT-Bench](https://arxiv.org/html/2608.27021v1), [Kentik AI Advisor](https://kb.kentik.com/docs/ai-advisor), and [Cisco ThousandEyes Event Detection](https://developer.cisco.com/docs/thousandeyes/event-detection-api-overview/). These informed the design; they do not validate our prototype's accuracy. Requirements come from the [problem statement](docs/problem_statement.pdf).
