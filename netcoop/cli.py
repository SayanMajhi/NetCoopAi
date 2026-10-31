"""netcoop command-line interface.

    netcoop run <scenario> [--local] [--control] [--endpoint URL]   diagnose one scenario
    netcoop export-schemas      regenerate schemas/*.json and the PROTOCOL.md field table
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
    from netcoop.orchestrator.run import load_signatures, load_topology, run_incident, write_outputs

    try:
        fixture = json.loads(Path(args.scenario).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fixture = {"incident_id": Path(args.scenario).stem, "_unreadable": f"{type(exc).__name__}: {exc}"}
    result = run_incident(fixture, load_topology(Path(args.topology)), load_signatures(),
                          connectivity="local" if args.local else "remote",
                          endpoint=args.endpoint, control=args.control)
    if "_unreadable" in fixture:
        result.report.validation_errors.insert(0, f"cannot read scenario: {fixture['_unreadable']}")
    report_path, trace_path = write_outputs(result, Path(args.out), Path(args.traces))
    print(json.dumps(result.report.model_dump(mode="json"), indent=2))
    print(f"\nreport: {report_path}\ntrace:  {trace_path}", file=sys.stderr)
    return 0


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

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
