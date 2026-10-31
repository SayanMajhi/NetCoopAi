"""B4: congestion findings use fresh, real evidence and configured limits."""

import json
import unittest
from pathlib import Path

from netcoop.agents.base import BaseAgent, IncidentContext
from netcoop.agents.performance import PerformanceAgent
from netcoop.normalizer.normalize import normalize
from netcoop.protocol.models import Envelope, MessageType, TaskPayload


ROOT = Path(__file__).resolve().parents[1]
TOPOLOGY = json.loads((ROOT / "scenarios" / "topology.json").read_text(encoding="utf-8"))
SIGNATURES = {"window": {"minutes": 5, "congestion_min_consecutive_samples": 3}, "thresholds": {
    "link_utilization_pct": {"warn": 80, "critical": 90},
    "latency_ms": {"warn": 100, "critical": 200},
    "jitter_ms": {"warn": 20, "critical": 50},
    "packet_loss_pct": {"warn": 1.0, "critical": 3.0},
    "throughput_mbps": {"warn": 0.5, "critical": 0.25},
}}


def _fixture(utilization, *, latency=None, drops=None, jitter=None, loss=None):
    records = []
    for index, value in enumerate(utilization, 1):
        timestamp = f"2026-11-01T10:{index:02d}:00Z"
        records.append({"timestamp": timestamp, "link_id": "HOSTEL_UPLINK_01",
                        "source_id": "switch-telemetry", "metrics": {"link_utilization_pct": value,
                                                                  "throughput_mbps": value * 10 if value is not None else None}})
        if latency is not None:
            metrics = {"latency_ms": latency[index - 1]}
            if jitter is not None:
                metrics["jitter_ms"] = jitter[index - 1]
            if loss is not None:
                metrics["packet_loss_pct"] = loss[index - 1]
            records.append({"timestamp": timestamp, "device_id": "SW_CAMPUS_3",
                            "source_id": "client-probe", "metrics": metrics})
        if drops is not None:
            records.append({"timestamp": timestamp, "device_id": "SW_CAMPUS_1",
                            "source_id": "switch-telemetry", "metrics": {"queue_drops": drops[index - 1]}})
    return {"incident_id": "INC-0101", "window_end": "2026-11-01T10:05:00Z", "records": records}


def _run(fixture, signatures=SIGNATURES):
    normalized = normalize(fixture, TOPOLOGY, signatures)
    assert not normalized.validation_errors, normalized.validation_errors
    ctx = IncidentContext(fixture["incident_id"], normalized.window_end, normalized.observations,
                          TOPOLOGY, signatures, normalized.normalization_log)
    sender = BaseAgent()
    sender.agent_id, sender.abbrev = "orchestrator", "ORCH"
    payload = TaskPayload(task_id="INC-0101-T-PERFORMANCE", capability="analyze_performance")
    task = sender.make(ctx, "performance_agent", MessageType.TASK, payload.model_dump(mode="json"), None)
    findings = PerformanceAgent().handle(task, ctx)
    return ctx, task, findings


class PerformanceTests(unittest.TestCase):
    def test_congestion_with_latency_and_quality_context(self):
        fixture = _fixture([95, 96, 97, 96, 95], latency=[120, 130, 140, 150, 160],
                           jitter=[22, 25, 26, 30, 28], loss=[1.1, 1.2, 1.5, 1.4, 1.2])
        ctx, task, findings = _run(fixture)
        self.assertEqual(1, len(findings))
        finding = Envelope.model_validate(findings[0].model_dump(mode="json"))
        self.assertEqual("supports", finding.payload["stance"])
        self.assertEqual("link_congestion@HOSTEL_UPLINK_01", finding.payload["hypothesis"])
        self.assertEqual(task.message_id, finding.in_reply_to)
        self.assertIn("latency p95", finding.payload["reason"])
        self.assertIn("jitter_ms", finding.payload["reason"])
        self.assertIn("packet_loss_pct", finding.payload["reason"])
        self.assertIn("throughput", finding.payload["reason"])
        self.assertTrue(all(ctx.evidence(eid) for eid in finding.payload["evidence_ids"]))
        self.assertEqual(findings[0].model_dump(mode="json"), _run(fixture)[2][0].model_dump(mode="json"))

    def test_queue_drops_can_corroborate_without_latency(self):
        ctx, _, findings = _run(_fixture([95, 96, 97, 96, 95], drops=[100, 105, 105, 110, 120]))
        self.assertEqual(1, len(findings))
        self.assertEqual("supports", findings[0].payload["stance"])
        self.assertIn("queue-drop counter rose", findings[0].payload["reason"])
        self.assertTrue(any(ctx.evidence(eid).metric == "queue_drops" for eid in findings[0].payload["evidence_ids"]))

    def test_unrelated_latency_does_not_corroborate_hostel_link(self):
        fixture = _fixture([95, 96, 97, 96, 95])
        fixture["records"].append({"timestamp": "2026-11-01T10:04:00Z", "device_id": "SW_CAMPUS_7",
                                   "metrics": {"latency_ms": 300}})
        _, _, findings = _run(fixture)
        self.assertEqual([], findings)

    def test_counter_reset_does_not_corroborate_congestion(self):
        _, _, findings = _run(_fixture([95, 96, 97, 96, 95], drops=[100, 5, 4, 3, 2]))
        self.assertEqual([], findings)

    def test_normal_utilization_contradicts_congestion(self):
        _, _, findings = _run(_fixture([35, 38, 40, 42, 39]))
        self.assertEqual(1, len(findings))
        self.assertEqual("contradicts", findings[0].payload["stance"])

    def test_missing_sample_breaks_sustained_run(self):
        _, _, findings = _run(_fixture([95, 96, None, 97, 98], latency=[120] * 5))
        self.assertEqual([], findings)

    def test_unset_thresholds_produce_no_claim(self):
        _, _, findings = _run(_fixture([95, 96, 97, 96, 95], latency=[120] * 5),
                              {"window": {"minutes": 5, "congestion_min_consecutive_samples": 3}, "thresholds": {}})
        self.assertEqual([], findings)


if __name__ == "__main__":
    unittest.main()
