from __future__ import annotations

from pathlib import Path

from thoth.protocol.schema_export import SCHEMA_MODELS, render_schema


def test_protocol_schema_snapshots_are_current() -> None:
    root = Path(__file__).resolve().parents[2] / "schemas" / "protocol"
    assert {path.name for path in root.glob("*.schema.json")} == set(SCHEMA_MODELS)
    for filename, model in SCHEMA_MODELS.items():
        assert (root / filename).read_text(encoding="utf-8") == render_schema(model)
