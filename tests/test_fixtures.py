"""scenarios/dev/* — the labelled incidents every agent is tested and evaluated on."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

from netcoop.normalizer.normalize import normalize
from netcoop.orchestrator.run import Components, load_signatures, load_topology, run_incident
from netcoop.protocol.models import Report

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "scenarios" / "dev"
LABELS = json.loads((ROOT / "scenarios" / "labels.json").read_text(encoding="utf-8"))["scenarios"]
TOPOLOGY, SIGNATURES = load_topology(), load_signatures()
FIXTURES = sorted(p.name for p in DEV.glob("*.json"))
READABLE = [n for n in FIXTURES if n != "malformed_input.json"]
STALE_BY_DESIGN = {"missing_stale.json"}
SPARSE_BY_DESIGN = {"unsupported_2_x.json", "missing_message_field.json"}   # too little data on purpose

sys.path.insert(0, str(ROOT / "scenarios"))
import build_dev_fixtures  # noqa: E402


def load(name: str) -> dict:
    return json.loads((DEV / name).read_text(encoding="utf-8"))


def test_every_label_has_a_fixture_and_back() -> None:
    assert set(FIXTURES) == set(LABELS)


def test_builder_is_deterministic(tmp_path: Path) -> None:
    for path in build_dev_fixtures.build(tmp_path):
        assert path.read_bytes() == (DEV / path.name).read_bytes(), f"{path.name}: rerun scenarios/build_dev_fixtures.py"


def test_malformed_fixture_is_not_json() -> None:
    with pytest.raises(json.JSONDecodeError):
        load("malformed_input.json")


@pytest.mark.parametrize("name", READABLE)
def test_fixture_normalizes_cleanly(name: str) -> None:
    result = normalize(load(name), TOPOLOGY, SIGNATURES)
    assert result.validation_errors == [], result.validation_errors[:3]
    assert result.observations and result.window_end is not None


@pytest.mark.parametrize("name", sorted(set(READABLE) - SPARSE_BY_DESIGN))
def test_enough_samples_and_sources(name: str) -> None:
    obs = normalize(load(name), TOPOLOGY, SIGNATURES).observations
    per_series = Counter((o.device_id or o.link_id, o.metric, o.source_id) for o in obs if "derived" not in o.quality_flags)
    assert min(per_series.values()) >= 5, "every series needs >= 5 samples for windows and counter deltas"
    assert len({o.source_id for o in obs}) >= 2, "at least two independent sources"
    fresh = [o for o in obs if "stale" not in o.quality_flags]
    if name not in STALE_BY_DESIGN:
        assert len(fresh) == len(obs), "only missing_stale.json may contain stale readings"


def test_stale_fixture_really_is_stale() -> None:
    obs = normalize(load("missing_stale.json"), TOPOLOGY, SIGNATURES).observations
    assert any("stale" in o.quality_flags for o in obs)
    assert any("missing" in o.quality_flags for o in obs)


def test_unit_mismatch_fixture_is_converted() -> None:
    result = normalize(load("unit_mismatch.json"), TOPOLOGY, SIGNATURES)
    flags = {f for o in result.observations for f in o.quality_flags}
    assert {"unit_converted", "derived"} <= flags


def test_counter_reset_fixture_is_flagged() -> None:
    obs = normalize(load("counter_reset.json"), TOPOLOGY, SIGNATURES).observations
    assert any("counter_reset" in o.quality_flags for o in obs)


def test_affected_ids_of_labels_appear_in_their_fixture() -> None:
    for name, label in LABELS.items():
        if name == "malformed_input.json" or not label["affected_ids"]:
            continue
        text = (DEV / name).read_text(encoding="utf-8")
        missing = [i for i in label["affected_ids"] if f'"{i}"' not in text]
        assert not missing, f"{name}: labelled IDs absent from the fixture: {missing}"


@pytest.mark.parametrize("name", FIXTURES)
def test_pipeline_returns_a_valid_report(name: str) -> None:
    # Normalizer only: proves every fixture runs end to end whatever agents are merged.
    try:
        fixture = load(name)
    except json.JSONDecodeError:
        fixture = {"incident_id": name.removesuffix(".json")}
    result = run_incident(fixture, TOPOLOGY, SIGNATURES,
                          components=Components(normalize=normalize, agents={}, connectivity=None))
    Report.model_validate(result.report.model_dump(mode="json"))
    expected_invalid = name == "malformed_input.json"
    assert (result.report.status == "invalid_input") == expected_invalid
