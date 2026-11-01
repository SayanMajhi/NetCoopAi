"""Phase 4 P4-5: `netcoop eval` scores and publishes every ARCHITECTURE §7 metric."""
from __future__ import annotations

import json

import pytest

from netcoop.cli import main
from netcoop.evaluation.evaluate import evaluate, outcome

LABEL = {"status": "diagnosed", "cause": "link_congestion"}


@pytest.mark.parametrize("cls,label,status,cause,expected", [
    ("link_congestion", LABEL, "diagnosed", "link_congestion", "exact"),
    ("link_congestion", LABEL, "probable", "link_congestion", "right cause"),
    ("link_congestion", LABEL, "diagnosed", "interface_fault", "wrong diagnosis"),
    ("link_congestion", LABEL, "probable", "interface_fault", "hedged wrong"),
    ("link_congestion", LABEL, "insufficient_evidence", None, "abstained"),
    ("healthy", {"status": "healthy", "cause": None}, "healthy", None, "exact"),
    ("healthy", {"status": "healthy", "cause": None}, "insufficient_evidence", None, "no-claim ok"),
    ("busy_healthy", {"status": "insufficient_evidence", "cause": None}, "probable", "link_congestion",
     "false diagnosis"),
    ("weak_wifi", LABEL, "failed", None, "error"),
])
def test_outcome_categories(cls, label, status, cause, expected):
    assert outcome(cls, label, status, cause) == expected


def test_small_eval_publishes_every_metric(tmp_path):
    s = evaluate(16, [11], out=tmp_path)
    for key in ("class_counts", "confusion_matrix", "accuracy_overall_exact", "accuracy_named_causes",
                "affected_device_accuracy", "healthy_false_diagnosis_rate", "healthy_abstention_rate",
                "abstention_rate_by_class", "evidence_link_integrity", "messages_by_type", "error_codes",
                "seeds", "wrong_diagnoses", "hedged_wrong", "status_by_variant"):
        assert key in s, key
    assert s["cases"] == 16 and s["seeds"] == [11]
    assert s["healthy_false_diagnosis_rate"] == 0.0 and s["wrong_diagnoses"] == 0
    assert s["evidence_link_integrity"] == 1.0
    assert {"summary.md", "summary.json", "cases.json"} <= {p.name for p in tmp_path.iterdir()}
    assert len(json.loads((tmp_path / "cases.json").read_text(encoding="utf-8"))) == 16


def test_cli_eval(tmp_path, capsys):
    assert main(["eval", "--n", "8", "--seed", "12", "--out", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["cases"] == 8
