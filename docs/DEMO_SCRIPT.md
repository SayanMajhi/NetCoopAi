# NetCoop AI demo script (4:00)

Every command below was run on this repository. Run from the repository root with the virtual environment active, in a font of at least 16 points.

## Before recording

Terminal 1 stays open with Connectivity. Terminal 2 runs the commands. The dashboard is a browser tab.

```bash
uvicorn connectivity_service.app:app --port 8001
```

```bash
netcoop demo --endpoint http://127.0.0.1:8001
```

```bash
netcoop run scenarios/dev/congestion.json --control
```

```bash
netcoop run scenarios/dev/ambiguous_congestion_errors.json --control
```

```bash
npm --prefix dashboard run dev
```

`netcoop demo --endpoint` is what makes the dashboard show "separate process, HTTP 200". Without `--endpoint`, Connectivity runs in-process and the dashboard says so. Open http://localhost:5173 after these have run.

## Script

| Time | Beat | Say | Show |
|---|---|---|---|
| 0:00–0:25 | **Problem** | Campus faults are spread across switches, probes and service checks. Several causes look alike, and a confident wrong answer sends an engineer to fix the wrong thing. NetCoop gives one evidence-based answer, or says it cannot. | README, first lines |
| 0:25–0:55 | **Architecture** | Five agents, one validated protocol. Performance and Infrastructure find; Connectivity interprets them in its own process; Correlation re-checks and queries; Reporting publishes. They are deterministic rule-based agents. | README Mermaid diagram, then terminal 1 with `uvicorn` running |
| 0:55–1:45 | **A diagnosis** | Run it live. Status, cause, cited evidence with thresholds, affected devices, and a limitation stating this is one window and not a probability. | Terminal 2: `netcoop run scenarios/dev/congestion.json`. Dashboard: **Congestion**, top card and Supporting evidence |
| 1:45–2:40 | **Real cooperation** | Timeline: Performance and Infrastructure each name the uplink. The highlighted Connectivity finding `F-CONN-03` lists the two peer messages it consumed and adds a blast radius of 6 devices, "a degradation, not an outage". It cites only its own checks. In the control run nobody told it, so the finding does not exist and the score drops from 1.00 to 0.88. Be exact: the status does not change here. | Dashboard: Agent communication, then **Why the exchange matters** |
| 2:40–3:15 | **Conflict** | Congestion and an interface fault both pass on one uplink. Interface fault leads. Correlation queries the owner of each key measurement; the error rate is only at warn, utilization is critical, and the ranking flips. The lead is still 0.03, under the 0.15 gate, so it reports `probable`, names the rival and says verify first. | Dashboard: **Ambiguous congestion errors**, Verification rounds and Competing causes. Then **Missing stale**: no cause named |
| 3:15–3:40 | **Evidence** | Held-out cases from a generator that imports no decision code. Zero false diagnoses at every noise level. These come from our own fault model, so they do not show field accuracy. The first run found a real bug, which is in the write-up. | Terminal 2: `netcoop eval --n 48 --seed 7 --noise 0.05` (about 2 s). Then `docs/results/EVALUATION.md` table |
| 3:40–4:00 | **Close** | What is proved: agents exchange validated messages over a real process boundary, peer findings change another agent's output, and the system abstains when it should. What is not: real telemetry and calibrated confidence. | Dashboard: Limitations and raw JSON |

## Fallbacks

Say out loud when you switch to a recorded run.

| Failure | Do this |
|---|---|
| Connectivity will not start or port 8001 is busy | Add `--local` to `netcoop run`, and say Connectivity is in-process for this take. The dashboard shows it as in-process |
| Dashboard fails | Open `reports/INC-0103.md` and `reports/INC-0103--control.md` as text. Compare the limitations and confidence lines |
| Live environment fails entirely | Use the recorded two-process runs below, and label them as recorded |
| A beat goes wrong | Record beats separately and join them |

Recorded runs are in [`docs/demo/recorded/`](demo/recorded/). To load them into the dashboard:

```bash
cp docs/demo/recorded/reports/* reports/
```

```bash
cp docs/demo/recorded/traces/* traces/
```

In Windows PowerShell, `cp` works the same way with these paths.

Showing that a failure is handled, if asked: stop terminal 1 and rerun `netcoop run scenarios/dev/congestion.json`. The CLI warns, the trace holds `AGENT_TIMEOUT`, and the status drops to `probable` because nobody judged scope or paths.

## Answers to likely questions

**Why several agents and not one rules engine?** Each agent owns different evidence and exchanges typed findings with provenance, so one can argue with another and be checked. We did not benchmark a centralized design, and on the held-out set the control run changes no outcomes, so we claim interoperability and explainability, not higher accuracy. What we can show is that findings cross a validated boundary, that a specific peer message produces a specific new finding, and that removing it removes the finding and lowers the leading score (in 6 of the 16 labelled scenarios that parse; the status never changes in them).

**Do the agents really communicate?** Each message goes through the bus. Connectivity is a separate OS process, and `traces/<incident>.jsonl` contains the raw HTTP request and response bodies. `consumed_message_ids` names the messages each finding used. You can call it yourself with `curl` and the bodies in `examples/wire/`.

**Is Connectivity truly independent?** It is an independent process that validates against the exported JSON Schema. It shares a repository and the protocol models with the rest, and the other four agents are in-process, so this is one application with one remote service.

**Could a replayed reading fake a sustained fault?** Copies at one instant count once, and a sustained run needs distinct instants over at least 60 seconds. A replay with fabricated new timestamps is not detectable from the data alone.

**What if verification disagrees with the window?** A probe that does not find the fault blocks `diagnosed`. The report caps it at `probable` and says the two disagree.

**Can a forged or stale message get in?** Each message is checked for incident, sender authority, cause on the sender's card, and a real request to reply to. Sender identity is not cryptographically authenticated.

**Is this AI? There is no LLM.** The agents are deterministic and rule-based, which the problem statement allows. The contribution is the interoperable protocol and evidence reconciliation.

**What does 0.95 mean?** It is the `heuristic-v1` score: a documented weighted formula multiplied by evidence coverage. It is not a probability and has not been calibrated. It is capped by status, and the uncapped score remains in the DECISION.

**How is it better than a rule-based monitoring script?** Thresholds alone cannot name the rival cause, refuse a busy-but-healthy link, or say what is missing. Here each conclusion cites evidence and the sensors behind it, conflicts are verified with a targeted query, and bad input becomes a typed error. It is still rules, not learning.

**How good is the dataset?** Synthetic. The organizer generator emits one reading per call with no labels, so we use it to prove format compatibility. Accuracy comes from our own generator. It differs from development data in seeds, timing, positions and metric mixes, but shares our model of what each fault looks like.

**What if the evaluation was tuned?** The generator was committed before any held-out result was seen, and it imports no decision code. The first run scored 237/240, and we published that. After a bug fix, confirmation used fresh seeds. Two later signature edits, for `weak_wifi` and `hardware_failure`, came after publication; re-running the same seeds gave identical metrics, and `EVALUATION.md` records this.

**What happens with bad input?** An unknown version, missing field or duplicate ID becomes a typed ERROR with a field path, listed in the report. One bad telemetry item is skipped and named. Missing data is never zero and blocks a `healthy` verdict.

**What if an agent fails?** A hung or crashing step becomes `AGENT_TIMEOUT` (5 second deadline) or `AGENT_FAILURE`. Without a Connectivity answer the run cannot be `diagnosed` or `healthy`. If Correlation fails twice, the report abstains and invents no score.

**Could this be deployed?** Not as it stands. It needs real collectors, a topology feed, tuned thresholds, calibrated confidence and streaming. It only reads data and takes no action on any network.
