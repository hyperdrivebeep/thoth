from __future__ import annotations

import hashlib
import importlib
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "schemas/protocol/public-method-catalog.json"


@pytest.fixture
def builder(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setattr(sys, "path", [str(ROOT / "scripts"), *sys.path])
    return importlib.import_module("build_public_method_catalog")


def test_build_reproduces_reviewed_catalog_and_digest(builder: ModuleType) -> None:
    reviewed = json.loads(CATALOG.read_text(encoding="utf-8"))
    generated = json.loads(json.dumps(builder.build()))
    assert generated["catalog_digest"] == reviewed["catalog_digest"]
    assert generated == reviewed


def test_main_roundtrips_only_to_temp_output(
    builder: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "generated" / "public-method-catalog.json"
    monkeypatch.setattr(builder, "OUTPUT", output)
    builder.main()
    first = output.read_bytes()
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(CATALOG.read_bytes()).hexdigest()
    assert json.loads(first) == json.loads(CATALOG.read_bytes())
    builder.main()
    assert output.read_bytes() == first


def test_missing_credential_metadata_fails_closed(
    builder: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    metadata = dict(builder.CREDENTIAL_METHOD_METADATA)
    del metadata["model/credential/login/status"]
    monkeypatch.setattr(builder, "CREDENTIAL_METHOD_METADATA", metadata)
    with pytest.raises(ValueError, match="credential metadata"):
        builder.build()


def test_incomplete_credential_metadata_fails_closed(
    builder: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    metadata = dict(builder.CREDENTIAL_METHOD_METADATA)
    status = metadata["model/credential/login/status"]
    metadata["model/credential/login/status"] = {**status, "policy": ""}
    monkeypatch.setattr(builder, "CREDENTIAL_METHOD_METADATA", metadata)
    with pytest.raises(ValueError, match="credential metadata"):
        builder.build()


def test_unknown_credential_method_fails_before_generic_fallback(
    builder: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = builder.parse_catalog

    def with_unknown(text: str) -> list[dict[str, str]]:
        return [
            *original(text),
            {"name": "model/credential/unreviewed", "namespace": "model", "surface": "QUERY"},
        ]

    monkeypatch.setattr(builder, "parse_catalog", with_unknown)
    with pytest.raises(ValueError, match="credential metadata"):
        builder.build()


def test_wrong_credential_surface_fails_closed(
    builder: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = builder.parse_catalog

    def wrong_surface(text: str) -> list[dict[str, str]]:
        rows = [dict(row) for row in original(text)]
        next(row for row in rows if row["name"] == "model/credential/login/status")["surface"] = (
            "COMMAND"
        )
        return rows

    monkeypatch.setattr(builder, "parse_catalog", wrong_surface)
    with pytest.raises(ValueError, match="credential surface"):
        builder.build()
