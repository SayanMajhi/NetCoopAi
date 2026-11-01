"""signatures-1.0.yaml must only use names the rest of the system agrees on."""
from pathlib import Path

import yaml

from netcoop.protocol.cards import CARDS
from netcoop.protocol.models import Cause

CONFIG = Path(__file__).resolve().parents[1] / "netcoop" / "config" / "signatures-1.0.yaml"
SIG = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
OWNED = {m for card in CARDS.values() for m in card.owned_metrics}


def _checks(sig: dict) -> list[dict]:
    items = list(sig.get("required", [])) + list(sig.get("corroborating", {}).get("checks", []))
    items += list(sig.get("contradicting", []))
    out = []
    for item in items:
        out.extend(item["any_of"] if "any_of" in item else [item])
    return out


def test_version():
    assert SIG["version"] == "1.0"


def test_every_signature_is_a_known_cause():
    assert set(SIG["signatures"]) <= {c.value for c in Cause}


def test_every_fault_cause_has_a_signature():
    faults = {c.value for c in Cause} - {"healthy", "upstream_dependency"}
    assert faults <= set(SIG["signatures"])


def test_checks_use_owned_metrics_and_frozen_vocabulary():
    checks, scopes = set(SIG["vocabulary"]["checks"]), set(SIG["vocabulary"]["scopes"])
    for name, sig in SIG["signatures"].items():
        for item in _checks(sig):
            assert set(item) <= {"metric", "check", "scope"}, f"{name}: unexpected keys {item}"
            assert item["metric"] in OWNED, f"{name}: metric {item['metric']} has no owning agent card"
            assert item["check"] in checks, f"{name}: check {item['check']} not in vocabulary"
            assert item.get("scope", "target") in scopes, f"{name}: scope {item.get('scope')} not in vocabulary"


def test_thresholds_only_for_owned_metrics():
    for metric in SIG["thresholds"]:
        assert metric in OWNED, f"threshold for {metric} but no agent card owns it"


def test_no_null_thresholds_and_each_has_a_rationale():
    for metric, th in SIG["thresholds"].items():
        assert th.get("warn") is not None and th.get("critical") is not None, f"{metric}: null threshold"
        assert isinstance(th.get("rationale"), str) and len(th["rationale"]) > 20, f"{metric}: rationale missing"
        assert th.get("direction"), f"{metric}: direction missing"


def test_warn_and_critical_are_ordered_by_direction():
    for metric, th in SIG["thresholds"].items():
        if th["direction"] in ("high", "rising_delta"):
            assert th["warn"] < th["critical"], metric
        elif th["direction"] in ("low", "low_relative_to_capacity"):
            assert th["warn"] > th["critical"], metric


def test_throughput_thresholds_are_fractions_of_capacity():
    th = SIG["thresholds"]["throughput_mbps"]
    assert th["basis"] == "fraction_of_capacity" and 0 < th["critical"] < th["warn"] < 1
