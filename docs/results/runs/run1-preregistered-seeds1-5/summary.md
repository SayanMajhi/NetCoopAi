# Held-out evaluation — 240 cases, seeds [1, 2, 3, 4, 5], noise σ 0.0

Frozen: thresholds, weights and gates from netcoop/config/signatures-1.0.yaml (unchanged since ed288c2).

| metric | value |
|---|---|
| accuracy overall exact | 98.8 % |
| accuracy overall acceptable | 99.2 % |
| accuracy named causes | 98.7 % |
| affected device accuracy | 100.0 % |
| healthy false diagnosis rate | 0.0 % |
| healthy abstention rate | 0.0 % |
| evidence link integrity | 100.0 % |
| wrong diagnoses (`diagnosed`, wrong cause) | 0 |
| hedged wrong (`probable`, wrong cause) | 0 |

## Per class

| class | n | outcomes | abstention | false diagnosis |
|---|---|---|---|---|
| healthy | 30 | exact 30 | 0.0 % | 0.0 % |
| busy_healthy | 30 | exact 30 | 100.0 % | 0.0 % |
| link_congestion | 30 | abstained 2, exact 27, right cause 1 | 6.7 % | — |
| interface_fault | 30 | exact 30 | 0.0 % | — |
| weak_wifi | 30 | exact 30 | 0.0 % | — |
| dns_failure | 30 | exact 30 | 0.0 % | — |
| dhcp_failure | 30 | exact 30 | 0.0 % | — |
| stale_telemetry | 30 | exact 30 | 100.0 % | 0.0 % |

## Confusion matrix (rows: true class; columns: answered cause, or status when no cause)

| true \ answered | dhcp_failure | dns_failure | healthy | insufficient_evidence | interface_fault | link_congestion | weak_wifi |
|---|---|---|---|---|---|---|---|
| healthy |  |  | 30 |  |  |  |  |
| busy_healthy |  |  |  | 30 |  |  |  |
| link_congestion |  |  |  | 2 |  | 28 |  |
| interface_fault |  |  |  |  | 30 |  |  |
| weak_wifi |  |  |  |  |  |  | 30 |
| dns_failure |  | 30 |  |  |  |  |  |
| dhcp_failure | 30 |  |  |  |  |  |  |
| stale_telemetry |  |  |  | 30 |  |  |  |

## Status by variant (P4-0 busy links, P4-3 one-source DNS/DHCP, metric mixes)

| class / variant | statuses |
|---|---|
| busy_healthy / spiky 80-93 % | insufficient_evidence 15 |
| busy_healthy / steady 84-93 % | insufficient_evidence 15 |
| dhcp_failure / one source (P4-3) | probable 8 |
| dhcp_failure / two sources | diagnosed 22 |
| dns_failure / one source (P4-3) | probable 13 |
| dns_failure / two sources | diagnosed 17 |
| healthy / complaint names an area | healthy 19 |
| healthy / complaint names nothing | healthy 11 |
| interface_fault / errors and flaps | diagnosed 6 |
| interface_fault / errors only | diagnosed 18 |
| interface_fault / flaps only | diagnosed 6 |
| link_congestion / drops only | probable 11 |
| link_congestion / latency and drops | diagnosed 7, probable 1 |
| link_congestion / latency only | diagnosed 9, insufficient_evidence 2 |
| stale_telemetry / congestion, utilization missing | insufficient_evidence 18 |
| stale_telemetry / interface fault, switch telemetry stale | insufficient_evidence 12 |
| weak_wifi / DEPT_AP_01 | diagnosed 30 |

## Protocol

Messages by type: {'DECISION': 240, 'FINDING': 3561, 'QUERY': 88, 'RESPONSE': 88, 'TASK': 720}  
ERROR codes: none

## Cases that were not exact (traces in `traces/`, inputs and reports in `cases/`)

| case | seed | class / variant | label | answer | outcome |
|---|---|---|---|---|---|
| link_congestion-018 | 30018 | link_congestion / latency only | diagnosed link_congestion | insufficient_evidence  0.0 | abstained |
| link_congestion-034 | 30034 | link_congestion / latency and drops | diagnosed link_congestion | probable link_congestion@ACC_LINK_03 0.88 | right cause |
| link_congestion-026 | 50026 | link_congestion / latency only | diagnosed link_congestion | insufficient_evidence  0.0 | abstained |
