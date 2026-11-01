"""`netcoop eval`: run the held-out set and publish every metric ARCHITECTURE §7 asks for (P4-5).

Thresholds, weights and gates are whatever signatures-1.0.yaml says (frozen at `ed288c2`); this
module only scores. Outcome of one case, judged against the generator's label:

- exact            status and cause both match the label
- right cause      a fault class answered with the right cause but another status (e.g. probable)
- abstained        a fault class answered insufficient_evidence
- no-claim ok      a no-fault class answered healthy or insufficient_evidence (healthy class: only healthy is exact)
- wrong diagnosis  `diagnosed` with the wrong cause (on a fault class)
- hedged wrong     `probable` with the wrong cause (counted apart from wrong diagnoses)
- false diagnosis  any diagnosed/probable answer on a no-fault class (healthy, busy_healthy, stale_telemetry)
- error            failed / invalid_input
"""
from __future__ import annotations

import copy
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from netcoop.evaluation.heldout import CLASSES, LABEL_RULES, NO_FAULT, Case, generate

GAUGE = {"latency_ms", "jitter_ms", "packet_loss_pct", "throughput_mbps", "cpu_utilization_pct",
         "memory_pct", "retry_rate_pct", "rtt_sec", "throughput_kbps"}
NAMED = ("diagnosed", "probable")


def add_noise(fx: dict[str, Any], sigma: float, seed: int) -> dict[str, Any]:
    """AI 3's noise function (feedback3.md R3, as in tests/test_noise_robustness.py), on records and on_query."""
    rng = random.Random(seed)
    x = copy.deepcopy(fx)
    for r in x["records"] + x.get("on_query", []):
        for k, v in r["metrics"].items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                if k in GAUGE:
                    r["metrics"][k] = round(max(0, v * (1 + rng.gauss(0, sigma))), 3)
                elif k == "link_utilization_pct":
                    r["metrics"][k] = round(min(100, max(0, v * (1 + rng.gauss(0, sigma)))), 2)
                elif k == "rssi_dbm":
                    r["metrics"][k] = round(v + rng.gauss(0, sigma * 40), 1)
    return x


def outcome(cls: str, label: dict[str, Any], status: str, cause: str | None) -> str:
    if status in ("failed", "invalid_input"):
        return "error"
    if cls in NO_FAULT:
        if status in NAMED:
            return "false diagnosis"
        return "exact" if status == label["status"] else "no-claim ok"
    if status in NAMED and cause == label["cause"]:
        return "exact" if status == label["status"] else "right cause"
    if status == "diagnosed":
        return "wrong diagnosis"
    if status == "probable":
        return "hedged wrong"
    return "abstained"


def _evidence_integrity(case: Case, result: Any, topology: dict, signatures: dict) -> list[str]:
    """Every evidence ID the report or any FINDING/RESPONSE cites must exist in the incident."""
    from netcoop.normalizer.normalize import normalize
    norm = normalize(case.fixture, topology, signatures)
    known = {o.evidence_id for o in norm.observations} | {o.evidence_id for o in norm.on_demand}
    cited = [e.evidence_id for e in result.report.supporting_evidence]
    for m in result.messages:
        if _v(m.type) in ("FINDING", "RESPONSE"):
            cited += list(m.payload.get("evidence_ids") or [])
    return sorted({e for e in cited if e not in known})


def evaluate(n: int, seeds: list[int], sigma: float = 0.0, out: Path | None = None,
             endpoint: str | None = None) -> dict[str, Any]:
    from netcoop.orchestrator.run import default_components, load_signatures, load_topology, run_incident, write_outputs

    topology, signatures = load_topology(), load_signatures()
    components = default_components(connectivity="remote", endpoint=endpoint) if endpoint \
        else default_components(connectivity="local")
    rows: list[dict[str, Any]] = []
    messages, errors = Counter(), Counter()
    for seed in seeds:
        for case in generate(n, seed):
            if sigma:
                case.fixture = add_noise(case.fixture, sigma, case.seed)
            result = run_incident(case.fixture, topology, signatures, components=components)
            r = result.report
            cause = r.suspected_root_cause.split("@")[0] if r.suspected_root_cause else None
            got_ids = set(r.affected_devices) | set(r.affected_segments)
            row = {"name": case.name, "cls": case.cls, "seed": case.seed, "run_seed": seed,
                   "variant": case.label["variant"], "label_status": case.label["status"],
                   "label_cause": case.label["cause"], "status": _v(r.status),
                   "hypothesis": r.suspected_root_cause, "confidence": r.confidence,
                   "alternatives": list(r.alternative_causes),
                   "affected_ok": (set(case.label["affected_ids"]) == got_ids) if case.label["cause"] else None,
                   "dangling_evidence": _evidence_integrity(case, result, topology, signatures),
                   "limitations": list(r.limitations)}
            row["outcome"] = outcome(case.cls, case.label, row["status"], cause)
            for m in result.messages:
                messages[_v(m.type)] += 1
                if _v(m.type) == "ERROR":
                    errors[m.payload.get("code", "?")] += 1
            rows.append(row)
            if out is not None and row["outcome"] not in ("exact", "no-claim ok"):
                write_outputs(result, out / "cases", out / "traces")
                (out / "cases" / f"{r.incident_id}.input.json").write_text(
                    json.dumps(case.fixture, indent=1) + "\n", encoding="utf-8", newline="\n")
    summary = summarize(rows, n, seeds, sigma)
    summary["messages_by_type"], summary["error_codes"] = dict(sorted(messages.items())), dict(sorted(errors.items()))
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        (out / "cases.json").write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8", newline="\n")
        (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8", newline="\n")
        (out / "summary.md").write_text(to_markdown(summary, rows), encoding="utf-8", newline="\n")
    return summary


def _v(x: Any) -> str:
    return getattr(x, "value", x)


def _rate(k: int, n: int) -> float | None:
    return round(k / n, 3) if n else None


def summarize(rows: list[dict[str, Any]], n: int, seeds: list[int], sigma: float) -> dict[str, Any]:
    by_cls: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_cls[r["cls"]].append(r)
    faults = [r for r in rows if r["cls"] not in NO_FAULT]
    no_fault = [r for r in rows if r["cls"] in NO_FAULT]
    named_right = [r for r in faults if r["outcome"] in ("exact", "right cause")]
    predicted = lambda r: (r["hypothesis"].split("@")[0] if r["status"] in NAMED and r["hypothesis"] else r["status"])
    confusion: dict[str, Counter] = {c: Counter() for c in CLASSES}
    for r in rows:
        confusion[r["cls"]][predicted(r)] += 1
    variants: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        variants[f"{r['cls']} / {r['variant']}"][r["status"]] += 1
    return {
        "cases": len(rows), "n_per_seed": n, "seeds": seeds, "sigma": sigma,
        "frozen": "thresholds, weights and gates from netcoop/config/signatures-1.0.yaml (unchanged since ed288c2)",
        "class_counts": {c: len(by_cls[c]) for c in CLASSES},
        "outcomes": dict(Counter(r["outcome"] for r in rows)),
        "outcomes_by_class": {c: dict(Counter(r["outcome"] for r in by_cls[c])) for c in CLASSES},
        "accuracy_overall_exact": _rate(sum(r["outcome"] == "exact" for r in rows), len(rows)),
        "accuracy_overall_acceptable": _rate(sum(r["outcome"] in ("exact", "right cause", "no-claim ok") for r in rows),
                                             len(rows)),
        "accuracy_named_causes": _rate(len(named_right), len(faults)),
        "affected_device_accuracy": _rate(sum(bool(r["affected_ok"]) for r in named_right), len(named_right)),
        "healthy_false_diagnosis_rate": _rate(sum(r["outcome"] == "false diagnosis" for r in no_fault), len(no_fault)),
        "false_diagnosis_rate_by_class": {c: _rate(sum(r["outcome"] == "false diagnosis" for r in by_cls[c]),
                                                   len(by_cls[c])) for c in CLASSES if c in NO_FAULT},
        "healthy_abstention_rate": _rate(sum(r["status"] != "healthy" for r in by_cls["healthy"]), len(by_cls["healthy"])),
        "abstention_rate_by_class": {c: _rate(sum(r["status"] == "insufficient_evidence" for r in by_cls[c]),
                                              len(by_cls[c])) for c in CLASSES},
        "wrong_diagnoses": sum(r["outcome"] == "wrong diagnosis" for r in rows),
        "hedged_wrong": sum(r["outcome"] == "hedged wrong" for r in rows),
        "evidence_link_integrity": _rate(sum(not r["dangling_evidence"] for r in rows), len(rows)),
        "confusion_matrix": {c: dict(sorted(confusion[c].items())) for c in CLASSES},
        "status_by_variant": {k: dict(sorted(v.items())) for k, v in sorted(variants.items())},
        "label_rules": LABEL_RULES,
    }


def to_markdown(s: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    pct = lambda v: "n/a" if v is None else f"{100 * v:.1f} %"
    lines = [f"# Held-out evaluation — {s['cases']} cases, seeds {s['seeds']}, noise σ {s['sigma']}", "",
             f"Frozen: {s['frozen']}.", "", "| metric | value |", "|---|---|"]
    for key in ("accuracy_overall_exact", "accuracy_overall_acceptable", "accuracy_named_causes",
                "affected_device_accuracy", "healthy_false_diagnosis_rate", "healthy_abstention_rate",
                "evidence_link_integrity"):
        lines.append(f"| {key.replace('_', ' ')} | {pct(s[key])} |")
    lines += [f"| wrong diagnoses (`diagnosed`, wrong cause) | {s['wrong_diagnoses']} |",
              f"| hedged wrong (`probable`, wrong cause) | {s['hedged_wrong']} |", "",
              "## Per class", "", "| class | n | outcomes | abstention | false diagnosis |", "|---|---|---|---|---|"]
    for c in CLASSES:
        oc = ", ".join(f"{k} {v}" for k, v in sorted(s["outcomes_by_class"][c].items()))
        fd = s["false_diagnosis_rate_by_class"].get(c)
        lines.append(f"| {c} | {s['class_counts'][c]} | {oc} | {pct(s['abstention_rate_by_class'][c])} | "
                     f"{pct(fd) if c in NO_FAULT else '—'} |")
    cols = sorted({k for v in s["confusion_matrix"].values() for k in v})
    lines += ["", "## Confusion matrix (rows: true class; columns: answered cause, or status when no cause)", "",
              "| true \\ answered | " + " | ".join(cols) + " |", "|---" * (len(cols) + 1) + "|"]
    for c in CLASSES:
        lines.append(f"| {c} | " + " | ".join(str(s["confusion_matrix"][c].get(k, 0) or "") for k in cols) + " |")
    lines += ["", "## Status by variant (P4-0 busy links, P4-3 one-source DNS/DHCP, metric mixes)", "",
              "| class / variant | statuses |", "|---|---|"]
    lines += [f"| {k} | {', '.join(f'{a} {b}' for a, b in v.items())} |" for k, v in s["status_by_variant"].items()]
    lines += ["", "## Protocol", "", f"Messages by type: {s['messages_by_type']}  ", f"ERROR codes: {s['error_codes'] or 'none'}",
              "", "## Cases that were not exact (traces in `traces/`, inputs and reports in `cases/`)", "",
              "| case | seed | class / variant | label | answer | outcome |", "|---|---|---|---|---|---|"]
    for r in rows:
        if r["outcome"] not in ("exact", "no-claim ok"):
            lines.append(f"| {r['name']} | {r['seed']} | {r['cls']} / {r['variant']} | {r['label_status']} "
                         f"{r['label_cause'] or ''} | {r['status']} {r['hypothesis'] or ''} {r['confidence']} | "
                         f"{r['outcome']} |")
    return "\n".join(lines) + "\n"
