# Incident INC-0103: diagnosed

> Diagnosed link congestion on HOSTEL_UPLINK_01, affecting SW_CAMPUS_1: 6 evidence items from 2 independent sources agree (confidence 0.88).

**Suspected root cause:** `link_congestion@HOSTEL_UPLINK_01`  
**Confidence:** 0.88 (heuristic-v1, signatures 1.0)
**Fault location:** HOSTEL_UPLINK_01, SW_CAMPUS_1  
**Impact scope (6 devices):** HOSTEL_AP_01, SW_CAMPUS_1, SW_CAMPUS_3, SW_CAMPUS_4, SW_CAMPUS_5, SW_CAMPUS_6

## Evidence

| ID | metric | value | per interval | meaning | source |
|---|---|---|---|---|---|
| EV-047 | link_utilization_pct | 93.4 percent |  | above critical 90 percent | switch-telemetry |
| EV-049 | link_utilization_pct | 96.4 percent |  | above critical 90 percent | switch-telemetry |
| EV-051 | link_utilization_pct | 93.1 percent |  | above critical 90 percent | switch-telemetry |
| EV-078 | queue_drops | 2010.0 count | +530 | +530 in one interval, at or above critical 100 | switch-telemetry |
| EV-079 | queue_drops | 2620.0 count | +610 | +610 in one interval, at or above critical 100 | switch-telemetry |
| EV-120 | latency_ms | 220.2 ms |  | above critical 200 ms | client-probe |

## Recommended actions

- Check egress queue depth and top talkers on HOSTEL_UPLINK_01
- Rebalance or rate-limit bulk traffic, or add capacity to HOSTEL_UPLINK_01 if utilization stays high
- Confirm upstream links of HOSTEL_UPLINK_01 are not saturated too

## Limitations

- based on the 5-minute window ending 2026-11-01T10:05:00+00:00; confidence is the documented heuristic-v1 score, not a calibrated probability
- before Q-01: link_congestion@HOSTEL_UPLINK_01 0.84
- after Q-01: link_congestion@HOSTEL_UPLINK_01 0.88 (link_congestion@HOSTEL_UPLINK_01: all 3 utilization samples above 90%; from 3 new on-demand probe sample(s))
- control run: Connectivity received no peer findings

## Normalization

- records[10].metrics.cpu_utilization_pct: cpu_utilization_pct -> cpu_pct
- records[11].metrics.cpu_utilization_pct: cpu_utilization_pct -> cpu_pct
- records[12].metrics.cpu_utilization_pct: cpu_utilization_pct -> cpu_pct
- records[13].metrics.cpu_utilization_pct: cpu_utilization_pct -> cpu_pct
- records[14].metrics.cpu_utilization_pct: cpu_utilization_pct -> cpu_pct
- records[20].metrics.cpu_utilization_pct: cpu_utilization_pct -> cpu_pct
- records[21].metrics.cpu_utilization_pct: cpu_utilization_pct -> cpu_pct
- records[22].metrics.cpu_utilization_pct: cpu_utilization_pct -> cpu_pct
- records[23].metrics.cpu_utilization_pct: cpu_utilization_pct -> cpu_pct
- records[24].metrics.cpu_utilization_pct: cpu_utilization_pct -> cpu_pct

Findings: F-CONN-01, F-CONN-02, F-CONN-03, F-CONN-04, F-CONN-05, F-CONN-06, F-CONN-07, F-CONN-08, F-CONN-09, F-INF-01, F-INF-02, F-PERF-01, F-PERF-02, F-PERF-03 · trace: INC-0103-TRACE
