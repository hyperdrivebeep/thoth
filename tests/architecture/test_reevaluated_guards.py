from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from scripts.check_ocp_extensions import core_closed_errors, extension_manifest_errors

ROOT = Path(__file__).resolve().parents[2]


def mirror(root: Path) -> dict[str, Any]:
    for name in ("config", "src"):
        shutil.copytree(ROOT / name, root / name, ignore=shutil.ignore_patterns("__pycache__"))
    return json.loads((root / "config/architecture-conformance.json").read_text())


def test_factory_rejects_existing_unrelated_symbol(tmp_path: Path) -> None:
    manifest = mirror(tmp_path)
    next(x for x in manifest["extension_points"] if x["name"] == "MODEL")["factory_target"] = (
        "ClockPort"
    )
    (tmp_path / "config/architecture-conformance.json").write_text(json.dumps(manifest))
    assert extension_manifest_errors(tmp_path)


def test_unknown_vendor_branch_is_not_a_registry_extension(tmp_path: Path) -> None:
    (tmp_path / "handler.py").write_text(
        "def route(provider):\n    if provider == 'brand_new_vendor':\n        return 1\n"
    )
    assert core_closed_errors(tmp_path)
