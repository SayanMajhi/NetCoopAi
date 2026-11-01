# NetCoop AI dashboard

React + Vite view of NetCoop runs. Every message and report is validated in the browser against the generated JSON Schemas in `../schemas/` (the same contract the Python bus and the Connectivity REST service enforce).

```bash
netcoop demo          # from the repo root: writes reports/*.json and traces/*.jsonl
cd dashboard
npm install
npm run dev           # http://localhost:5173
```

What it shows for each run:
- the report status, one-line summary, confidence, affected devices, limitations, rejected inputs and recommended actions;
- every message of the incident trace (TASK, FINDING, QUERY, RESPONSE, DECISION, ERROR) with sender, recipient and schema check;
- the competing causes from Correlation's DECISION (uncapped heuristic-v1 score, coverage, supporting and contradicting findings);
- the cited evidence with its interpretation against the threshold.

The samples in `../examples/` are listed after the real runs (they are what you see on a fresh clone before `netcoop demo`).

`npm run gen:types` regenerates `src/types/` from `../schemas/`; CI fails if they drift.
