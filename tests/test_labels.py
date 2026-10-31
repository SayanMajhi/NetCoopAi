"""The answer key must be complete and use only real IDs, statuses and causes."""
import json
from pathlib import Path

from netcoop.protocol.models import Cause, ErrorCode, Status

ROOT = Path(__file__).resolve().parents[1]
LABELS = json.loads((ROOT / "scenarios" / "labels.json").read_text(encoding="utf-8"))["scenarios"]
TOPO = json.loads((ROOT / "scenarios" / "topology.json").read_text(encoding="utf-8"))
IDS = {n["id"] for n in TOPO["nodes"]} | {l["link_id"] for l in TOPO["links"]}

REQUIRED = {
    "normal", "false_report", "congestion", "weak_wifi", "interface_fault", "dns_failure",
    "dhcp_failure", "ambiguous_congestion_errors", "missing_stale", "unit_mismatch",
    "counter_reset", "schema_0_9", "unsupported_2_x", "missing_message_field",
    "duplicate_message", "correlation_timeout",
}


def test_all_required_classes_present():
    assert REQUIRED <= {name.removesuffix(".json") for name in LABELS}


def test_labels_use_real_values():
    for name, lab in LABELS.items():
        assert lab["status"] in {s.value for s in Status}, name
        assert set(lab["affected_ids"]) <= IDS, f"{name}: unknown IDs {set(lab['affected_ids']) - IDS}"
        assert set(lab["expected_errors"]) <= {e.value for e in ErrorCode}, name
        named = lab["status"] in ("diagnosed", "probable")
        assert (lab["cause"] in {c.value for c in Cause}) if named else lab["cause"] is None, name
        assert lab["why"], name


def test_every_status_except_failed_is_covered():
    assert {s.value for s in Status} - {"failed"} <= {lab["status"] for lab in LABELS.values()}