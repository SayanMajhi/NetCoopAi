"""Checks against the untouched organizer-generator files."""

import json
from pathlib import Path

import pytest

from netcoop.normalizer.adapter_generator import load_raw


RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "generator_raw"

RAW_FILES = [
    "normal_unseeded.json",
    "latency_spike_unseeded.json",
    "hardware_failure_unseeded.json",
]


@pytest.mark.parametrize("filename", RAW_FILES)
def test_load_raw_preserves_each_generator_metric(filename: str) -> None:
    path = RAW_DIR / filename
    original = json.loads(path.read_text(encoding="utf-8"))

    rows = load_raw(path)

    assert len(rows) == len(original["metrics"])
    assert {row["original_field"] for row in rows} == {
        f"metrics.{name}" for name in original["metrics"]
    }

    for row in rows:
        raw_name = row["original_field"].removeprefix("metrics.")
        assert row["original_value"] == original["metrics"][raw_name]
        assert row["value"] == original["metrics"][raw_name]
        assert row["device_id"] == original["device_id"]
        assert row["observed_at"] == original["timestamp"]
        assert row["source_id"] == "organizer-generator"
        assert row["metric"] != "status"


def test_alias_and_missing_metric_are_handled() -> None:
    normal = load_raw(RAW_DIR / "normal_unseeded.json")
    hardware = load_raw(RAW_DIR / "hardware_failure_unseeded.json")

    cpu = next(row for row in normal if row["original_field"] == "metrics.cpu_utilization_pct")
    assert cpu["metric"] == "cpu_pct"

    assert next(row for row in normal if row["metric"] == "packet_loss_pct")["value"] == 0.0
    assert not any(row["metric"] == "jitter_ms" for row in hardware)


def test_bad_input_has_a_clear_error(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.json"
    bad_file.write_text('{"timestamp": "2026-11-01T00:00:00Z", "device_id": "SW_1"}')

    with pytest.raises(ValueError, match="needs metrics"):
        load_raw(bad_file)