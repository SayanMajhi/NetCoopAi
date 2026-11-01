"""P4-4: the held-out generator itself (structure only; no decision code runs here)."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from netcoop.evaluation.heldout import CLASSES, NO_FAULT, generate
from netcoop.normalizer.normalize import normalize
from netcoop.orchestrator.run import load_signatures, load_topology

ROOT = Path(__file__).resolve().parents[1]
DEV_LABELS = json.loads((ROOT / "scenarios" / "labels.json").read_text(encoding="utf-8"))["scenarios"]
TOPOLOGY = load_topology()
IDS = {n["id"] for n in TOPOLOGY["nodes"]} | {l["link_id"] for l in TOPOLOGY["links"]}


def test_deterministic_and_seed_dependent():
    a, b, c = generate(24, 7), generate(24, 7), generate(24, 8)
    assert [x.fixture for x in a] == [x.fixture for x in b]
    assert [x.fixture for x in a] != [x.fixture for x in c]


def test_balanced_classes_with_busy_healthy_as_its_own_class():
    counts = Counter(c.cls for c in generate(48, 1))
    assert set(counts) == set(CLASSES) and len(set(counts.values())) == 1
    assert "busy_healthy" in CLASSES and "busy_healthy" in NO_FAULT


def test_differs_from_dev_in_seeds_times_and_positions():
    cases = generate(80, 3)
    dev_seeds = set(range(101, 117))
    assert not dev_seeds & {c.seed for c in cases}
    assert all(not c.fixture["window_end"].startswith("2026-11-01") for c in cases)
    dev_targets = {(v["cause"], v["affected_ids"][0]) for v in DEV_LABELS.values() if v.get("cause")}
    for c in cases:
        if c.label["cause"]:
            assert (c.label["cause"], c.label["affected_ids"][0]) not in dev_targets or c.label["cause"] in (
                "dns_failure", "dhcp_failure"), (c.name, c.label)


def test_labels_are_well_formed_and_reference_the_topology():
    for c in generate(80, 5):
        lab = c.label
        assert lab["status"] in ("healthy", "diagnosed", "probable", "insufficient_evidence")
        assert (lab["cause"] is None) == (c.cls in NO_FAULT)
        assert set(lab["affected_ids"]) <= IDS


def test_one_source_service_cases_exist_and_expect_probable():
    service = [c for c in generate(160, 2) if c.cls in ("dns_failure", "dhcp_failure")]
    one = [c for c in service if c.label["variant"].startswith("one source")]
    assert one and all(c.label["status"] == "probable" for c in one)
    for c in one:
        reach = {r["source_id"] for r in c.fixture["records"] if "reachability" in r["metrics"]}
        assert reach == {"client-probe"}


def test_every_fixture_is_valid_input():
    signatures = load_signatures()
    for c in generate(40, 9):
        assert not normalize(c.fixture, TOPOLOGY, signatures).validation_errors, c.name
