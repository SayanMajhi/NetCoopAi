# NetCoop AI — demo script (target 4:00, hard limit 5:00)

Rule: show only what actually works on the recording day. Every beat has its exact command.
Run from a fresh terminal, font 16+, terminal left and dashboard right.

| Time | Beat | What the viewer sees | Command / view | Built in |
|---|---|---|---|---|
| 0:00–0:20 | **Problem** | One sentence: campus faults are spread across devices and formats; 5 agents must agree on one evidence-based answer | title slide | — |
| 0:20–0:45 | **Real data in** | Untouched organizer generator file, its field mapping, the adapter output with `original_field` kept | `type data\generator_raw\hardware_failure_unseeded.json`, `data/field_mapping.md` | Phase 1 ✅ |
| 0:45–1:15 | **One protocol** | `netcoop/1.0` envelope; a FINDING without `stance` is rejected with `field_path: payload.stance` | `pytest tests/test_protocol.py -q`, `docs/m1_curl_proof.md` | Phase 1 ✅ |
| 1:15–1:35 | **Version handling** | 0.9 message upgraded with a visible record; 2.0 message gets `SCHEMA_VERSION_UNSUPPORTED` | `netcoop run scenarios/dev/schema_0_9.json` | Phase 3 |
| 1:35–2:20 | **Agents really collaborate** | Connectivity runs as a separate REST process; it receives Performance/Infrastructure findings and changes its scope; the control run without them gives a different answer | terminal 1: `uvicorn connectivity_service.app:app --port 8001`; terminal 2: `netcoop run scenarios/dev/congestion.json` then `--control` | Phase 2 |
| 2:20–3:00 | **Conflict resolution** | Ambiguous case: congestion vs interface errors; Correlation sends a QUERY, the answer changes the ranking; report says `probable` with the rival named | `netcoop run scenarios/dev/ambiguous_congestion_errors.json` + dashboard event feed | Phase 3 |
| 3:00–3:25 | **Honesty** | Missing/stale data gives `insufficient_evidence` with no named cause | `netcoop run scenarios/dev/missing_stale.json` | Phase 3 |
| 3:25–3:50 | **Results** | Held-out evaluation: confusion matrix, false-diagnosis rate on healthy, abstention rate | `netcoop eval --n 50 --noise --seed 7` | Phase 4 |
| 3:50–4:00 | **Close** | Final JSON with evidence IDs; repo link | dashboard report view | Phase 5 |

## Backup plan
- If the REST process fails live: use the pre-recorded trace in `traces/` and say so.
- If the dashboard is not ready: show the same JSON in the terminal (`netcoop run ... | more`).
- Record each beat separately and join them; never re-run the whole demo for one mistake.

## Before recording
- [ ] `pytest -q` green on `main`
- [ ] each command above run once on a clean clone
- [ ] traces for every beat saved in `traces/`