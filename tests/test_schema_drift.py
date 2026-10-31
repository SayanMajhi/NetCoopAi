"""CI gate: checked-in schemas must equal what models.py generates."""
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from netcoop.protocol.export import build_all, dumps

ROOT = Path(__file__).resolve().parents[1]


def test_schemas_match_models():
    for name, schema in build_all().items():
        checked_in = ROOT / "schemas" / name
        assert checked_in.exists(), f"{name} missing — run `netcoop export-schemas`"
        assert checked_in.read_text(encoding="utf-8") == dumps(schema), (
            f"{name} drifted from models.py — run `netcoop export-schemas` and commit"
        )


def test_exported_message_schema_validates_examples_like_python():
    v = Draft202012Validator(json.loads((ROOT / "schemas" / "message-1.0.json").read_text(encoding="utf-8")))
    ok = json.loads((ROOT / "examples" / "finding.json").read_text(encoding="utf-8"))
    assert not list(v.iter_errors(ok))
    del ok["payload"]["stance"]
    assert list(v.iter_errors(ok)), "JSON Schema must also reject a FINDING without stance"
