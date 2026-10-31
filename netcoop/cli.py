"""netcoop command-line interface.

    netcoop export-schemas      regenerate schemas/*.json and the PROTOCOL.md field table
"""
from __future__ import annotations

import argparse

from netcoop import __version__


def cmd_export(_: argparse.Namespace) -> int:
    from netcoop.protocol.export import main as export_main
    export_main([])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="netcoop", description="NetCoop AI multi-agent diagnosis")
    parser.add_argument("--version", action="version", version=f"netcoop {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("export-schemas", help="regenerate schemas/ and PROTOCOL.md from models.py").set_defaults(func=cmd_export)
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
