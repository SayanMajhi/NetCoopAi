"""netcoop command-line interface."""
from __future__ import annotations

import argparse

from netcoop import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="netcoop", description="NetCoop AI multi-agent diagnosis")
    parser.add_argument("--version", action="version", version=f"netcoop {__version__}")
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
