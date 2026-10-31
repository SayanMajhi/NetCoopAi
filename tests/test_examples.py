"""examples/ are the demo artifact: every ID must exist in the topology and every
metric must be a canonical metric some agent card owns."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from netcoop.protocol.cards import CARDS

ROOT = Path(__file__).resolve().parents[1]
TOPO = json.loads((ROOT / "scenarios" / "topology.json").read_text(encoding="utf-8"))
TOPOLOGY_IDS = {n["id"] for n in TOPO["nodes"]} | {link["link_id"] for link in TOPO["links"]}
CANONICAL_METRICS = {m for card in CARDS.values() for m in card.owned_metrics}

# The 0.9 and 2.0 samples are deliberately non-conforming.
EXAMPLES = sorted(p.name for p in (ROOT / "examples").glob("*.json") if p.name not in {"finding_v0_9.json", "message_v2_0.json"})


def _target(hypothesis: str) -> str:
    return hypothesis.split("@", 1)[1]


def ids_and_metrics(body: dict[str, Any]) -> tuple[set[str], set[str]]:
    """Device/link IDs and metric names an example refers to."""
    p = body.get("payload", {})
    ids: set[str] = set(p.get("scope_ids", [])) | set(body.get("affected_devices", [])) | set(body.get("affected_segments", []))
    hypotheses = [p.get("hypothesis"), p.get("target_hypothesis"), body.get("suspected_root_cause"),
                  *p.get("related_hypotheses", []), *(r["hypothesis"] for r in p.get("ranking", [])),
                  *body.get("alternative_causes", [])]
    ids |= {_target(h) for h in hypotheses if h}
    if p.get("target_id"):
        ids.add(p["target_id"])
    metrics = {e["metric"] for e in body.get("supporting_evidence", [])}
    if p.get("desired_measurement"):
        metrics.add(p["desired_measurement"])
    return ids, metrics


@pytest.mark.parametrize("name", EXAMPLES)
def test_example_ids_exist_in_topology(name: str) -> None:
    ids, _ = ids_and_metrics(json.loads((ROOT / "examples" / name).read_text(encoding="utf-8")))
    assert ids <= TOPOLOGY_IDS, f"{name}: not in scenarios/topology.json: {sorted(ids - TOPOLOGY_IDS)}"


@pytest.mark.parametrize("name", EXAMPLES)
def test_example_metrics_are_canonical(name: str) -> None:
    _, metrics = ids_and_metrics(json.loads((ROOT / "examples" / name).read_text(encoding="utf-8")))
    assert metrics <= CANONICAL_METRICS, f"{name}: not canonical: {sorted(metrics - CANONICAL_METRICS)}"


def test_examples_cover_ids_at_all() -> None:
    # Guards against the walker silently finding nothing.
    ids, metrics = ids_and_metrics(json.loads((ROOT / "examples" / "report_diagnosed.json").read_text(encoding="utf-8")))
    assert "SW_CAMPUS_1" in ids and "HOSTEL_UPLINK_01" in ids and "queue_drops" in metrics
