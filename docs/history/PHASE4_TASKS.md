# Phase 4 (Milestone M4) — Robustness + evaluation

**Base:** Phase 3 ACCEPTED on `ed288c2` (contract `contract-1.0.2`). **Branch:** `m4/sayan-evaluation`. **Team:** PM (AI 1) builds; AI 3 reviews.
**Goal (ARCHITECTURE §7, feedback3.md §2–§3):** fix the known false-positive path first, then measure the system on a held-out set it was not built on, and publish every §7 metric with at least one failure shown in full.

## Entry conditions from the Phase 3 re-review, and what was done

| # | Item | Done | Where |
|---|---|---|---|
| P4-0a | `_rising_drops` counts a pair only if it rose ≥ `queue_drops.warn` (10) in that interval; reset pairs skipped; no threshold → no drop corroboration | ✅ `2470c56` | `netcoop/agents/performance.py`, `tests/test_performance.py` |
| P4-0b | Corroboration not met (fewer than `min_matches` corroborating checks pass) → neither `diagnosed` nor `probable`; limitation *"…: corroboration not met (needs queue_drops rising ≥ 10/interval or latency above 100 ms)"* and a collect action | ✅ `2470c56` | `netcoop/agents/correlation.py`, `reporting.py`, `tests/test_phase4_busy_link.py` (AI 3's three variants → `insufficient_evidence`; the gate also holds when an agent over-nominates) |
| P4-1 | Per-interval delta next to every cumulative counter: `EvidenceItem.interval_delta` (null for first sample, gap, reset) and a "per interval" column in the Markdown | ✅ `3c08e7a` | **contract-1.0.3** (additive optional field): models → `export-schemas` → `npm run gen:types` → parity green; tag after merge |
| P4-2 | Healthy gate's missing/stale rule scoped to the area the complaint names (topology ID, zone or distinctive label word, plus path to core, devices behind it, DNS/DHCP); whole network when it names nothing; ignored out-of-area gaps listed as a limitation. Healthy abstention rate reported by `netcoop eval` | ✅ `3c08e7a` | `correlation.py::_area`, `tests/test_phase4_report_scope.py` |
| P4-3 | Held-out DNS/DHCP cases with DNS and reachability from one source (≈ 40 %), expected `probable` | ✅ | generator; effect in `docs/results/PHASE4_RESULTS.md` |
| P4-4 | Held-out generator with new seeds, times, positions and metric mixes, committed before any held-out result | ✅ `fe2e9ee` | `netcoop/evaluation/heldout.py`, `tests/test_heldout_generator.py` |
| P4-5 | `netcoop eval`: class counts, confusion matrix, accuracy overall / named causes, affected-device accuracy, false-diagnosis rate by no-fault class, abstention by class, evidence-link integrity, protocol and ERROR counts, seeds, traces of every non-exact case, hedged-wrong counted apart from wrong diagnoses | ✅ | `netcoop/evaluation/evaluate.py`, `tests/test_eval.py`, results in `docs/results/` |
| P4-6 | Contract rule kept: the one model change (P4-1) went through regenerate + parity + new tag | ✅ | `PROTOCOL.md` contract line |

## Decisions recorded during the phase
- **Held-out protocol.** Generator committed first (`fe2e9ee`). Run 1 (seeds 1–5) is published as the pre-registered result, misses included. It exposed one bug, which was fixed; the fix was then confirmed on seeds 6–10, which had not been run before.
- **Bug found by the held-out set: poll jitter read as a missing interval.** `_streak` (Performance) and `sustained_run` (Correlation) used the smallest sample spacing as the poll interval, so an ordinary late sample (73 s after a 45 s one) broke a sustained run. Both now use the median spacing; a sample that is really missing still ends the run (`tests/test_poll_jitter.py`). Decision logic only: no threshold, weight or gate changed.
- **Not tuned:** abstentions under σ ≥ 0.10 when noisy throughput contradicts measured utilization (the R2 cross-check marks the sample `malformed`); AI 3's dev σ 0.15 wrong diagnoses; dev σ 0.10 seed 7 hedge.
- **Known limit:** the held-out set is our generator's model of each fault. It shows generalisation across positions, timing, mixes and noise, not performance on real telemetry.

## Acceptance evidence
- `pytest -q` green (476 tests); `export-schemas` + `build_dev_fixtures.py` leave the tree clean; 17/17 dev labels; `tests/test_noise_robustness.py` unchanged and green (σ 0.05 → 100 exact; σ 0.10 → 93/6/1 hedged).
- `git diff ed288c2 -- netcoop/config/` empty.
- `netcoop eval --n 48 --seed 6 7 8 9 10 --noise S` for S ∈ {0, 0.05, 0.10, 0.15}: 0 false diagnoses, 0 wrong diagnoses, 0 hedged wrong, 100 % affected IDs, 100 % evidence integrity, 0 ERROR messages; σ 0 repeated over REST with identical rows.
