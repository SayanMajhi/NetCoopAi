# Phase 4 results — held-out evaluation

Everything ARCHITECTURE §7 asks for, produced by `netcoop eval`. Each run directory under
[`phase4/`](phase4/) has `summary.md` (tables), `summary.json` (every metric), `cases.json` (one row per case:
label, answer, outcome, affected-ID check, dangling evidence), and, for every case that was not exact, its input
fixture, report (JSON and Markdown) and full message trace.

**Frozen:** thresholds, weights and gates in `netcoop/config/signatures-1.0.yaml` are unchanged since `ed288c2`
(`git diff ed288c2 -- netcoop/config/` is empty). Connectivity runs in-process; run 3 at σ 0 was repeated against
the live REST service (`--endpoint http://127.0.0.1:8001`) and gave identical rows for all 240 cases.

## How the held-out set was made (P4-4)

- Generator: [`netcoop/evaluation/heldout.py`](../../netcoop/evaluation/heldout.py), committed in `fe2e9ee`
  **before any held-out case was run**. It imports no agent, decision code, `labels.json` or YAML; each label
  follows from the fault it overlays, by the rules in `LABEL_RULES` (repeated in every `summary.json`).
- 8 classes, round-robin, so every class has the same count: `healthy`, **`busy_healthy`** (P4-0: 80–93 %
  utilization, spiky or steady, normal latency, background drops < 10/interval), `link_congestion`,
  `interface_fault`, `weak_wifi`, `dns_failure`, `dhcp_failure`, `stale_telemetry`.
- Different from `scenarios/dev`: seeds `S × 10 000 + i` (dev: 101–116); window ends on 2026-11-02..09 with poll
  jitter (samples 40–80 s apart; dev: exactly 60 s on 11-01); congestion on access links, interface faults on links
  dev never faults, weak Wi-Fi on `DEPT_AP_01`, probe and DNS/DHCP-client positions drawn at random; metric mixes
  (congestion seen via latency only / drops only / both; interface faults via errors / flaps / both; P4-3
  one-source DNS/DHCP + reachability; stale or missing key telemetry).
- Seeds used: run 1 and 2: 1–5; run 3: 6–10. 48 cases per seed, 240 per run.

## Run 1 — pre-registered (seeds 1–5, σ 0), before any fix

| metric | value |
|---|---|
| exact (status and cause) | 237 / 240 = 98.8 % |
| named-cause accuracy (fault classes) | 98.7 % |
| affected-device accuracy (right cause) | 100 % |
| **false diagnosis on no-fault classes** | **0 / 90** (healthy 0/30, busy_healthy 0/30, stale_telemetry 0/30) |
| healthy abstention rate | 0 % |
| wrong diagnoses / hedged wrong | 0 / 0 |
| evidence-link integrity | 100 % |
| ERROR messages | 0 |

The 3 misses were all `link_congestion` (traces in `phase4/run1-preregistered-seeds1-5/`): two abstained
(INC-H3-018, INC-H5-026, "latency only") and one was `probable` instead of `diagnosed` (INC-H3-034).

**Root cause (a bug, not a threshold):** Performance (`_streak`) and Correlation (`sustained_run`) took the poll
interval to be the *smallest* spacing between samples. With real poll jitter (INC-H3-034: 45, 73, 57, 68 s) the
interval became 45 s, so a 73 s spacing (> 1.5 × 45) counted as a missing sample and broke the sustained run;
the dev fixtures, with exact 60 s spacing, never showed it. **Fix:** the interval is the median spacing; a sample
that is really missing (≈ 2 × the interval) still ends the run (`tests/test_poll_jitter.py`). No threshold,
weight or gate changed. Because seeds 1–5 had now been seen, the fix was confirmed on fresh seeds 6–10 (run 3).

## Run 2 — seeds 1–5 again, after the fix

240 / 240 exact; every other metric as in run 1.

## Run 3 — fresh seeds 6–10 (never looked at before the fix)

| σ (AI 3's noise) | exact | right cause, other status | abstained | wrong diagnosis | hedged wrong | false diagnosis (no-fault) | healthy abstention | affected IDs | evidence integrity |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 240 | 0 | 0 | 0 | 0 | 0 / 90 | 0 % | 100 % | 100 % |
| 0.05 | 240 | 0 | 0 | 0 | 0 | 0 / 90 | 0 % | 100 % | 100 % |
| 0.10 | 238 | 0 | 2 | 0 | 0 | 0 / 90 | 0 % | 100 % | 100 % |
| 0.15 *(beyond the requirement)* | 232 | 2 | 6 | 0 | 0 | 0 / 90 | 0 % | 100 % | 100 % |

Overall exact accuracy: 100 %, 100 %, 99.2 %, 96.7 %. Named-cause accuracy: 100 %, 100 %, 98.7 %, 96.0 %.
Abstention by class at σ 0.15: `link_congestion` 20 %, `busy_healthy` and `stale_telemetry` 100 % (the expected
answer), every other class 0 %. Confusion matrices, per-class outcomes and status-by-variant tables are in each
`summary.md`.

Protocol counts (run 3, σ 0): 720 TASK, 3 571 FINDING, 90 QUERY, 90 RESPONSE, 240 DECISION, **0 ERROR**.

### A wrong / abstained case, with its trace

`run3-fresh-seeds6-10-sigma0.10/cases/INC-H8-018` (seed 80018, congestion on `ACC_LINK_07`, latency only).
Noise moved throughput independently of utilization: at 13:50:03 the measured utilization is 100 % but
throughput/capacity says 78 %. The normalizer's cross-check (Phase 3 R2) marks the measured sample `malformed`
because the two disagree on the verdict (critical vs warn), which leaves a hole in the run, so no sustained run of 3
remains and the system answers `insufficient_evidence` with "collect fresh measurements". With contradicting
sources, abstaining is the intended answer; it is **recorded, not tuned**. INC-H8-026 is the same pattern.

## P4-3 — one source for DNS/DHCP and reachability

| variant | answers (run 3, σ 0) | answers (run 1) |
|---|---|---|
| DNS, two sources (ICMP poller + client probe) | diagnosed 20 / 20 | diagnosed 17 / 17 |
| DNS, one source (client probe only) | **probable 10 / 10** | probable 13 / 13 |
| DHCP, two sources | diagnosed 22 / 22 | diagnosed 22 / 22 |
| DHCP, one source | **probable 8 / 8** | probable 8 / 8 |

Effect: with DNS checks and reachability from the same source, the right cause is still found every time, but the
answer is capped at `probable` (limitation "evidence from fewer than two independent raw sources"), as intended.

## P4-0 — busy but healthy links

`busy_healthy` (30 per run, half spiky 80–93 %, half steady 84–93 %): `insufficient_evidence` in every run and at
every σ; **0 false diagnoses**. Before the P4-0 fix, AI 3's spiky variant gave `probable link_congestion 0.74`.

## Known behaviour, recorded and not tuned

- Dev noise sweep, σ 0.15 (AI 3, seeds 11–40): 2 wrong diagnoses on `ambiguous_congestion_errors` (e.g. seed 18),
  where noisy utilization (86.8, 100, 76.7, 100, 55.6) really does refute sustained congestion while the interface
  signature passes. Defensible; not tuned.
- Dev σ 0.10 seed 7: hedged `probable interface_fault` with `link_congestion` as the alternative. AI 3 confirmed
  this is the right answer; `tests/test_noise_robustness.py` is unchanged.
- Held-out σ ≥ 0.10: congestion abstains when noisy throughput contradicts measured utilization (above).

## Honest limits of these numbers

- The held-out set comes from **our own generator**. It differs from dev in seeds, timing, positions and metric
  mixes, but it shares our model of what each fault looks like (same sources, similar ranges). High scores show
  the logic generalises across positions, timing and noise, not that it would score the same on real campus
  telemetry or on a different team's fault model.
- Classes are clear-cut by construction except `busy_healthy` and `stale_telemetry`; there are no held-out
  two-fault (ambiguous) incidents. Those exist only in dev (`ambiguous_congestion_errors`).
- Reproduce: `netcoop eval --n 48 --seed 6 7 8 9 10 --noise 0.10` (writes `eval/…`; deterministic).
