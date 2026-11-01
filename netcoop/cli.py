"""netcoop command-line interface.

    netcoop run <scenario> [--local] [--control] [--endpoint URL]   diagnose one scenario
    netcoop export-schemas      regenerate schemas/*.json and the PROTOCOL.md field table
    netcoop eval [--n 48] [--seed S ...] [--noise SIGMA] [--out DIR]   score the held-out set (Phase 4)
    netcoop demo [--endpoint URL]   run every scenario in scenarios/dev and compare with its label
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from netcoop import __version__


def cmd_export(_: argparse.Namespace) -> int:
    from netcoop.protocol.export import main as export_main
    export_main([])
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from netcoop.agents.reporting import summarize
    from netcoop.orchestrator.run import load_signatures, load_topology, run_incident, write_outputs

    unreadable = None
    try:
        fixture = json.loads(Path(args.scenario).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fixture, unreadable = None, f"{type(exc).__name__}: {exc}"
    result = run_incident(fixture, load_topology(Path(args.topology)), load_signatures(),
                          connectivity="local" if args.local else "remote",
                          endpoint=args.endpoint, control=args.control,
                          incident_id=f"INC-{Path(args.scenario).stem}")   # used when the file has none
    if unreadable:
        result.report.validation_errors[:] = [f"cannot read scenario: {unreadable}"]
        result.report.summary = summarize(result.report)
    report_path, trace_path = write_outputs(result, Path(args.out), Path(args.traces), control=args.control)
    print(json.dumps(result.report.model_dump(mode="json"), indent=2))
    print(f"\nreport: {report_path}\ntrace:  {trace_path}", file=sys.stderr)
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from netcoop.evaluation.evaluate import evaluate

    out = Path(args.out) / (f"n{args.n}-seeds{'-'.join(map(str, args.seed))}-sigma{args.noise:g}")
    summary = evaluate(args.n, args.seed, sigma=args.noise, out=out,
                       endpoint=args.endpoint)
    keys = ("cases", "accuracy_overall_exact", "accuracy_named_causes", "affected_device_accuracy",
            "healthy_false_diagnosis_rate", "healthy_abstention_rate", "wrong_diagnoses", "hedged_wrong",
            "evidence_link_integrity", "outcomes", "error_codes")
    print(json.dumps({k: summary[k] for k in keys}, indent=2))
    print(f"\nsummary: {out / 'summary.md'}", file=sys.stderr)
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """One command for judges: run every labelled dev scenario, print outcome vs expected label.
    labels.json is read here only, for scoring; the pipeline never sees it."""
    from netcoop.orchestrator.run import load_signatures, load_topology, run_incident, write_outputs

    root = Path(args.scenarios)
    labels = json.loads((root.parent / "labels.json").read_text(encoding="utf-8"))["scenarios"]
    topology, signatures = load_topology(), load_signatures()
    rows, matched = [], 0
    for name, label in labels.items():
        try:
            fixture = json.loads((root / name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            fixture, unreadable = None, f"cannot read scenario: {type(exc).__name__}"
        else:
            unreadable = None
        result = run_incident(fixture, topology, signatures, connectivity="remote" if args.endpoint else "local",
                              endpoint=args.endpoint or "", incident_id=f"INC-{Path(name).stem}")
        if unreadable:
            result.report.validation_errors[:] = [unreadable]
        write_outputs(result, Path(args.out), Path(args.traces))
        r = result.report
        cause = (r.suspected_root_cause or "").split("@")[0] or None
        codes = sorted({e.split(" ")[0] for e in r.validation_errors if e[:1].isupper() and "_" in e.split(" ")[0]})
        ok = r.status == label["status"] and cause == label["cause"] and             all(c in codes for c in label.get("expected_errors", []) if c != "AGENT_TIMEOUT")
        matched += ok
        rows.append((name.removesuffix(".json"), f"{label['status']} {label['cause'] or ''}".strip(),
                     f"{r.status} {cause or ''}".strip(), "-" if r.confidence is None else f"{r.confidence:.2f}",
                     ", ".join(codes) or "-", "PASS" if ok else "FAIL"))
    head = ("scenario", "expected", "got", "conf", "rejected inputs", "")
    widths = [max(len(str(x[i])) for x in rows + [head]) for i in range(len(head))]
    for row in [head] + rows:
        print("  ".join(str(c).ljust(w) for c, w in zip(row, widths)))
    print(f"\n{matched}/{len(rows)} scenarios match their label - reports in {args.out}/, traces in {args.traces}/")
    return 0 if matched == len(rows) else 1


def main(argv: list[str] | None = None) -> int:
    from netcoop.orchestrator.run import DEFAULT_ENDPOINT, DEFAULT_TOPOLOGY

    parser = argparse.ArgumentParser(prog="netcoop", description="NetCoop AI multi-agent diagnosis")
    parser.add_argument("--version", action="version", version=f"netcoop {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("export-schemas", help="regenerate schemas/ and PROTOCOL.md from models.py").set_defaults(func=cmd_export)

    run = sub.add_parser("run", help="diagnose one scenario file; writes reports/ and traces/")
    run.add_argument("scenario", help="path to a scenario JSON, e.g. scenarios/dev/congestion.json")
    run.add_argument("--local", action="store_true", help="call the Connectivity agent in-process (tests only)")
    run.add_argument("--control", action="store_true", help="send Connectivity no peer findings")
    run.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="Connectivity agent base URL")
    run.add_argument("--topology", default=str(DEFAULT_TOPOLOGY))
    run.add_argument("--out", default="reports")
    run.add_argument("--traces", default="traces")
    run.set_defaults(func=cmd_run)

    ev = sub.add_parser("eval", help="generate the held-out set, run it, publish metrics (ARCHITECTURE §7)")
    ev.add_argument("--n", type=int, default=48, help="cases per seed (8 classes, round-robin)")
    ev.add_argument("--seed", type=int, nargs="+", default=[1], help="one or more generator seeds")
    ev.add_argument("--noise", type=float, default=0.0, help="AI 3's multiplicative noise sigma, e.g. 0.05")
    ev.add_argument("--endpoint", default=None, help="score against a running Connectivity service instead")
    ev.add_argument("--out", default="eval")
    ev.set_defaults(func=cmd_eval)

    demo = sub.add_parser("demo", help="run every labelled dev scenario and compare with its label")
    demo.add_argument("--scenarios", default="scenarios/dev")
    demo.add_argument("--endpoint", default=None, help="use a running Connectivity service (default: in-process)")
    demo.add_argument("--out", default="reports")
    demo.add_argument("--traces", default="traces")
    demo.set_defaults(func=cmd_demo)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
