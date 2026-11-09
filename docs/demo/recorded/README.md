# Recorded demo runs

Real output, kept as a fallback and for inspection without running anything. **These are recorded runs, not live ones.** Say so if you show them.

They were produced on 2026-11-09 with the Connectivity agent running as a separate `uvicorn` process on port 8001, so each trace holds the raw HTTP request and response bodies.

| Incident | Scenario | Command |
|---|---|---|
| `INC-0103` | `congestion` | `netcoop demo --endpoint http://127.0.0.1:8001` |
| `INC-0103--control` | `congestion`, no peer findings | `netcoop run scenarios/dev/congestion.json --control` |
| `INC-0108` | `ambiguous_congestion_errors` | `netcoop demo --endpoint http://127.0.0.1:8001` |
| `INC-0108--control` | `ambiguous_congestion_errors`, no peer findings | `netcoop run scenarios/dev/ambiguous_congestion_errors.json --control` |
| `INC-0109` | `missing_stale` | `netcoop demo --endpoint http://127.0.0.1:8001` |

Message IDs and evidence IDs are deterministic, so rerunning these commands gives the same conclusions.
