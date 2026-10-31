"""B2 normalization contract and provenance checks."""

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from netcoop.normalizer.adapter_generator import load_raw
from netcoop.normalizer.normalize import normalize


ROOT = Path(__file__).resolve().parents[1]
TOPOLOGY = json.loads((ROOT / "scenarios" / "topology.json").read_text(encoding="utf-8"))
SIGNATURES = {"window": {"minutes": 5}}


class NormalizeTests(unittest.TestCase):
    def test_alias_conversion_missing_and_deterministic_ids(self):
        fixture = {
            "window_end": "2026-11-01T10:05:00Z",
            "records": [
                {"timestamp": "2026-11-01T10:04:00Z", "device_id": "SW_CAMPUS_3", "metrics": {"rtt_sec": 0.12, "packet_loss_pct": None}},
                {"timestamp": "2026-11-01T10:04:00Z", "link_id": "HOSTEL_UPLINK_01", "metrics": {"throughput_kbps": 960000}},
            ],
        }
        result = normalize(fixture, TOPOLOGY, SIGNATURES)
        self.assertEqual([], result.validation_errors)
        self.assertEqual(datetime(2026, 11, 1, 10, 5, tzinfo=timezone.utc), result.window_end)
        self.assertEqual(["EV-001", "EV-002", "EV-003", "EV-004"], [o.evidence_id for o in result.observations])
        latency = next(o for o in result.observations if o.metric == "latency_ms")
        self.assertEqual(120, latency.value)
        self.assertEqual(0.12, latency.original_value)
        self.assertIn("alias_mapped", latency.quality_flags)
        self.assertIn("unit_converted", latency.quality_flags)
        missing = next(o for o in result.observations if o.metric == "packet_loss_pct")
        self.assertIsNone(missing.value)
        self.assertIn("missing", missing.quality_flags)
        derived = next(o for o in result.observations if o.metric == "link_utilization_pct")
        self.assertEqual(96, derived.value)
        self.assertIn("derived", derived.quality_flags)
        self.assertEqual([o.model_dump(mode="json") for o in result.observations],
                         [o.model_dump(mode="json") for o in normalize(fixture, TOPOLOGY, SIGNATURES).observations])

    def test_stale_malformed_and_counter_reset(self):
        fixture = {"window_end": "2026-11-01T10:05:00Z", "records": [
            {"timestamp": "2026-11-01T09:59:00Z", "device_id": "SW_CAMPUS_1", "metrics": {"queue_drops": 10}},
            {"timestamp": "2026-11-01T10:01:00Z", "device_id": "SW_CAMPUS_1", "metrics": {"queue_drops": 100}},
            {"timestamp": "2026-11-01T10:02:00Z", "device_id": "SW_CAMPUS_1", "metrics": {"queue_drops": 3, "cpu_pct": "broken"}},
        ]}
        result = normalize(fixture, TOPOLOGY, SIGNATURES)
        self.assertEqual([], result.validation_errors)
        counters = sorted((o for o in result.observations if o.metric == "queue_drops"), key=lambda o: o.observed_at)
        self.assertIn("stale", counters[0].quality_flags)
        self.assertIn("counter_reset", counters[-1].quality_flags)
        cpu = next(o for o in result.observations if o.metric == "cpu_pct")
        self.assertIsNone(cpu.value)
        self.assertIn("malformed", cpu.quality_flags)

    def test_bad_input_is_reported_without_exception(self):
        bad = {"window_end": "2026-11-01T10:05:00Z", "records": [
            {"timestamp": "2026-11-01T10:04:00Z", "device_id": "SW_CAMPUS_1", "metrics": {"mystery": 5}},
        ]}
        result = normalize(bad, TOPOLOGY, SIGNATURES)
        self.assertEqual([], result.observations)
        self.assertIn("unknown raw metric", result.validation_errors[0])
        unreadable = normalize(ROOT / "missing-fixture.json", TOPOLOGY, SIGNATURES)
        self.assertEqual([], unreadable.observations)
        self.assertTrue(unreadable.validation_errors)

    def test_untouched_generator_record(self):
        for name in ("normal_unseeded.json", "latency_spike_unseeded.json", "hardware_failure_unseeded.json"):
            with self.subTest(name=name):
                path = ROOT / "data" / "generator_raw" / name
                raw = json.loads(path.read_text(encoding="utf-8"))
                result = normalize(raw, TOPOLOGY, SIGNATURES)
                self.assertEqual([], result.validation_errors)
                self.assertEqual(len(raw["metrics"]), len(result.observations))
                self.assertEqual(raw["timestamp"], result.window_end.isoformat().replace("+00:00", "Z"))
                adapter_rows = load_raw(path)
                self.assertEqual(len(raw["metrics"]), len(adapter_rows))
                self.assertTrue(all(row["original_value"] == raw["metrics"][row["original_field"].removeprefix("metrics.")]
                                    for row in adapter_rows))

    def test_utilization_cross_check_keeps_raw_value_but_flags_discrepancy(self):
        fixture = {"window_end": "2026-11-01T10:05:00Z", "records": [
            {"timestamp": "2026-11-01T10:04:00Z", "link_id": "HOSTEL_UPLINK_01",
             "metrics": {"throughput_mbps": 500, "link_utilization_pct": 90}},
        ]}
        result = normalize(fixture, TOPOLOGY, SIGNATURES)
        self.assertEqual([], result.validation_errors)
        measured = next(o for o in result.observations if o.metric == "link_utilization_pct")
        self.assertEqual(90, measured.value)
        self.assertIn("malformed", measured.quality_flags)
        self.assertTrue(any("differs" in entry for entry in result.normalization_log))

    def test_legacy_adapter_also_reads_fixture_wrapper(self):
        fixture = {"window_end": "2026-11-01T10:05:00Z", "records": [
            {"timestamp": "2026-11-01T10:04:00Z", "link_id": "HOSTEL_UPLINK_01",
             "source_id": "switch-telemetry", "metrics": {"throughput_kbps": 960000}},
        ]}
        with patch.object(Path, "read_text", return_value=json.dumps(fixture)):
            rows = load_raw("fixture.json")
        self.assertEqual(960, rows[0]["value"])
        self.assertEqual("HOSTEL_UPLINK_01", rows[0]["link_id"])
        self.assertEqual("switch-telemetry", rows[0]["source_id"])


if __name__ == "__main__":
    unittest.main()
