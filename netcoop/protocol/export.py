"""Generate schemas/*.json and the PROTOCOL.md field reference from models.py.

    python -m netcoop.protocol.export            # write into repo
    python -m netcoop.protocol.export --out DIR  # write elsewhere (drift test)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import BaseModel

from netcoop.protocol import models as m

REPO_ROOT = Path(__file__).resolve().parents[2]
BEGIN = "<!-- BEGIN GENERATED FIELD REFERENCE -->"
END = "<!-- END GENERATED FIELD REFERENCE -->"


def _schema(model: type[BaseModel], schema_id: str) -> dict:
    s = model.model_json_schema(mode="serialization")
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": schema_id, **s}


def message_schema() -> dict:
    """Envelope schema whose payload is checked per `type` (if/then), so non-Python
    receivers get the full wire contract from one file."""
    s = _schema(m.Envelope, "message-1.0.json")
    defs = s.setdefault("$defs", {})
    rules = []
    for type_name, model in m.PAYLOAD_MODELS.items():
        ps = model.model_json_schema(mode="serialization", ref_template="#/$defs/{model}")
        defs.update(ps.pop("$defs", {}))
        defs[model.__name__] = ps
        rules.append(
            {
                "if": {"properties": {"type": {"const": type_name}}, "required": ["type"]},
                "then": {"properties": {"payload": {"$ref": f"#/$defs/{model.__name__}"}}},
            }
        )
    s["allOf"] = rules
    return s


def build_all() -> dict[str, dict]:
    return {
        f"message-{m.SCHEMA_VERSION}.json": message_schema(),
        f"finding-{m.SCHEMA_VERSION}.json": _schema(m.FindingPayload, f"finding-{m.SCHEMA_VERSION}.json"),
        f"report-{m.SCHEMA_VERSION}.json": _schema(m.Report, f"report-{m.SCHEMA_VERSION}.json"),
        f"observation-{m.SCHEMA_VERSION}.json": _schema(m.Observation, f"observation-{m.SCHEMA_VERSION}.json"),
        f"bundle-{m.SCHEMA_VERSION}.json": _schema(m.Bundle, f"bundle-{m.SCHEMA_VERSION}.json"),
    }


def dumps(schema: dict) -> str:
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def field_reference() -> str:
    lines = [BEGIN, "", f"_Generated from `netcoop/protocol/models.py` ({m.PROTOCOL_VERSION}). Do not edit by hand._", ""]
    for model in (m.Envelope, *m.PAYLOAD_MODELS.values(), m.RankedCause, m.Observation, m.EvidenceItem, m.Report):
        lines += [f"### {model.__name__}", "", "| Field | Type | Required | Description |", "|---|---|---|---|"]
        props = model.model_json_schema(mode="serialization")
        required = set(props.get("required", []))
        for name, p in props.get("properties", {}).items():
            typ = p.get("type") or " / ".join(
                x.get("type", x.get("$ref", "").split("/")[-1]) for x in p.get("anyOf", [])
            ) or p.get("$ref", "").split("/")[-1]
            desc = p.get("description", "").replace("|", "\\|")
            lines.append(f"| `{name}` | {typ} | {'yes' if name in required else 'no'} | {desc} |")
        lines.append("")
    for enum in (m.MessageType, m.Stance, m.Status, m.Cause, m.ErrorCode, m.QualityFlag):
        lines.append(f"**{enum.__name__}:** " + ", ".join(f"`{e.value}`" for e in enum))
        lines.append("")
    lines.append(END)
    return "\n".join(lines)


def write(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, schema in build_all().items():
        (out_dir / name).write_text(dumps(schema), encoding="utf-8", newline="\n")


def update_protocol_md(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    start, end = text.index(BEGIN), text.index(END) + len(END)
    path.write_text(text[:start] + field_reference() + text[end:], encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "schemas")
    args = ap.parse_args(argv)
    write(args.out)
    if args.out == REPO_ROOT / "schemas":
        update_protocol_md(REPO_ROOT / "PROTOCOL.md")
    print(f"schemas written to {args.out}")


if __name__ == "__main__":
    main()
