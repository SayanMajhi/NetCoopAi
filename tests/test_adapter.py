"""Checks against the untouched organizer-generator files, through the runtime normalizer."""

import json
from pathlib import Path

import pytest

from netcoop.normalizer.normalize import normalize
from netcoop.orchestrator.run import load_signatures, load_topology

RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "generator_raw"
RAW_FILES = ["normal_unseeded.json", "latency_spike_unseeded.json", "hardware_failure_unseeded.json"]
TOPOLOGY, SIGNATURES = load_topology(), load_signatures()


def load(filename: str) -> tuple[dict, list]:
    original = json.loads((RAW_DIR / filename).read_text(encoding="utf-8"))
    return original, normalize(original, TOPOLOGY, SIGNATURES).observations


@pytest.mark.parametrize("filename", RAW_FILES)
def test_every_generator_metric_keeps_its_original_field_and_value(filename: str) -> None:
    original, observations = load(filename)
    measured = [o for o in observations if "derived" not in o.quality_flags]
    assert {o.original_field for o in measured} == {f"metrics.{name}" for name in original["metrics"]}
    for o in measured:
        assert o.original_value == original["metrics"][o.original_field.removeprefix("metrics.")]
        assert o.device_id == original["device_id"]
        assert o.source_id == "organizer-generator"
        assert o.metric != "status"


def test_alias_is_mapped_and_a_real_zero_stays_zero() -> None:
    _, normal = load("normal_unseeded.json")
    assert next(o for o in normal if o.original_field == "metrics.cpu_utilization_pct").metric == "cpu_pct"
    assert next(o for o in normal if o.metric == "packet_loss_pct").value == 0.0


def test_absent_metric_is_not_invented() -> None:
    _, hardware = load("hardware_failure_unseeded.json")
    assert not any(o.metric == "jitter_ms" for o in hardware)
