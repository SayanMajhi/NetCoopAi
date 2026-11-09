# Engineering history

Working documents from the 24-hour build, kept as they were written. They describe the system at the moment each one was written, so paths, test counts and statuses in them may be out of date. For the current system, read [README.md](../../README.md) and [ARCHITECTURE.md](../../ARCHITECTURE.md).

| File | What it records |
|---|---|
| [DESIGN_PLAN.md](DESIGN_PLAN.md) | The pre-implementation design, including optional features (MCP, HITL, LLM selection, simulated remediation) that were **not** built |
| [STATUS.md](STATUS.md) | Phase tracker and check-in log, including each review verdict and rejection |
| [PHASE1_TASKS.md](PHASE1_TASKS.md) to [PHASE4_TASKS.md](PHASE4_TASKS.md) | Task breakdown per member and the decisions taken in each phase |
| [README_track_C.md](README_track_C.md) | The original topology, bus and Connectivity notes, as written by the track owner. Current version: [CONNECTIVITY_SERVICE.md](../CONNECTIVITY_SERVICE.md) |
| [m1_curl_proof.md](m1_curl_proof.md), [m1_curl/](m1_curl/) | First proof of the REST boundary, against an endpoint shape that was later replaced by Bundles |

**AI 3** in these files is the AI model the team used as an independent phase reviewer; it accepted or rejected each phase against evidence. Files named `feedback*.md` in the log were review packs that were not committed.

`scenarios/labels.json` still refers to `docs/PHASE3_TASKS.md` in its change note. The file now lives here; labels are not edited after the fact.
