# NetCoop AI

Five specialist agents exchange **validatable evidence** over a versioned protocol (`netcoop/1.0`), challenge one another, and publish **one** evidence-based JSON diagnosis of a campus network fault. Data is synthetic; nothing touches a real network.

- Problem statement: [docs/problem_statement.pdf](docs/problem_statement.pdf)
- Architecture: [ARCHITECTURE.md](ARCHITECTURE.md) · context for AI coding assistants: [CLAUDE.md](CLAUDE.md)
- Phase plan and status: [docs/STATUS.md](docs/STATUS.md) · Phase 1 tasks: [docs/PHASE1_TASKS.md](docs/PHASE1_TASKS.md)
- Protocol and field reference: [PROTOCOL.md](PROTOCOL.md), generated JSON Schemas in [schemas/](schemas/)
- Organizer data and field mapping: [data/field_mapping.md](data/field_mapping.md)
- Connectivity REST service: [docs/README_track_C.md](docs/README_track_C.md)
- Demo plan: [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md)

## Setup (3 commands)
```bash
python -m venv .venv && .venv\Scripts\activate    # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

## Run
```bash
netcoop --version
netcoop export-schemas                              # regenerate schemas/ + PROTOCOL.md after editing models.py
uvicorn connectivity_service.app:app --port 8001    # Connectivity agent as its own process
```
Coming in later phases: `netcoop run <scenario>` (end-to-end diagnosis) and `netcoop eval --n 50 --noise` (held-out evaluation).

## How it works
| Component | Role |
|---|---|
| Normalizer | Converts raw telemetry to canonical observations; keeps original field/value; flags missing, stale, converted data (never turns missing into 0) |
| Orchestrator + bus | Validates **every** message, drops duplicates, turns every failure into a typed `ERROR`, logs all traffic to SQLite |
| Performance agent | Latency, jitter, loss, throughput, utilization |
| Infrastructure agent | CPU/memory, queue drops, interface errors, flaps, Wi-Fi signal/retries; emits supporting **and contradicting** findings |
| Connectivity agent | Separate REST process; topology, blast radius, DNS/DHCP, reachability; reads peer findings before answering |
| Correlation agent | Ranks causes, sends QUERY when evidence conflicts, abstains when evidence is thin (`heuristic-v1` confidence) |
| Reporting agent | Produces the final JSON report using only cited evidence |

Statuses: `diagnosed`, `probable`, `healthy`, `insufficient_evidence`, `invalid_input`, `failed`. Only the first two may name a cause.

## Repository layout
| Path | Contents |
|---|---|
| `netcoop/protocol/` | Protocol models (source of truth), schema export, version compatibility, agent cards |
| `netcoop/normalizer/` | Organizer-generator adapter |
| `netcoop/orchestrator/` | Message bus and SQLite event log |
| `netcoop/config/signatures-1.0.yaml` | Versioned thresholds, fault signatures, check vocabulary, decision gates |
| `connectivity_service/` | Connectivity agent REST service |
| `scenarios/` | Topology, labeled dev scenarios (`labels.json` is never read at runtime) |
| `examples/` | One valid message per type, old/new-version samples, report samples |

## Team workflow
- One branch per task (`m1/<name>-<topic>`), PR + one review, `pytest -q` green on `main`.
- Only the protocol lead edits `netcoop/protocol/models.py`; run `netcoop export-schemas` and commit the regenerated files in the same PR.

## Limitations
- The organizer generator returns one reading for one device per call, with no topology, repeated samples or fault labels. We use it to prove we ingest the official format untouched; diagnostic accuracy is measured on our own labeled scenarios (`scenarios/`).
- Thresholds are documented heuristics, not calibrated probabilities.
- _Held-out evaluation results: added in Phase 4._