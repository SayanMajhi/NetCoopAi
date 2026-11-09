# NetCoop AI dashboard

React and Vite view of NetCoop runs. Every message and report is validated in the browser against the generated JSON Schemas in `../schemas/`, the same contract the Python bus and the Connectivity service enforce.

From the repository root, generate runs first, then start the dashboard:

```bash
netcoop demo
```

```bash
npm --prefix dashboard ci
```

```bash
npm --prefix dashboard run dev
```

Open http://localhost:5173 and reload after new runs. Data is bundled from `../reports/` and `../traces/` when the page loads; there is no backend and no invented data. It is a local trace viewer, not an operational dashboard.

For each run it shows the status, root cause, confidence and its meaning, fault location, impact scope, supporting and contradicting evidence, competing causes, verification rounds, the full message timeline, limitations, follow-up checks and the raw JSON.

With `netcoop run <scenario> --control` alongside the normal run, **Why the exchange matters** compares what Connectivity concluded with and without peer findings.

Entries under **Schema samples** come from `../examples/` and are labelled as samples, not runs.

`npm run gen:types` regenerates `src/types/` from `../schemas/`; CI fails if they drift.
