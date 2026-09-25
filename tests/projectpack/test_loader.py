from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from thoth.adapters.projectpacks import ProjectPackError, load_project_pack


def _make_pack(root: Path, *, identifier: str, source_text: str) -> Path:
    root.mkdir(parents=True)
    (root / "sources").mkdir()
    (root / "future").mkdir()
    (root / "oracle").mkdir()
    raw = source_text.encode()
    (root / "sources" / "plan.md").write_bytes(raw)
    (root / "future" / "outcome-report.md").write_text(
        "FUTURE_CANARY_MUST_NOT_BE_READ", encoding="utf-8"
    )
    (root / "oracle" / "gold.json").write_text("not-valid-json", encoding="utf-8")
    (root / "project.yaml").write_text(
        f"""pack_id: pack:{identifier}
project_id: project:{identifier}
name: {identifier} project
cutoff_at: 2026-08-30T07:15:00Z
overlay: general-rnd
policy_binding_ref: policy:default
scenario:
  case_id: case:{identifier}
  thread_id: thread:{identifier}
  cycle_id: cycle:{identifier}
  object_id: object:{identifier}
  problem: Why does the observed result differ from the plan?
""",
        encoding="utf-8",
    )
    (root / "policy.yaml").write_text(
        """policy_version: policy:default
model_policy_ref: model-policy:scripted
external_write: false
allow_scripted_model: false
minimum_action_tier_by_family:
  READ_ONLY_ANALYSIS: R0
approver_role_by_family: {}
unknown_action_family_tier: R3
""",
        encoding="utf-8",
    )
    manifest = [
        {
            "source_id": f"source:{identifier}",
            "path": "plan.md",
            "media_type": "text/markdown",
            "authority": "OFFICIAL",
            "cutoff_state": "ELIGIBLE",
            "security_class": "INTERNAL",
            "byte_sha256": hashlib.sha256(raw).hexdigest(),
            "rights": "synthetic fixture",
        }
    ]
    (root / "source-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_loader_ignores_future_and_oracle_and_late_binds_sources(tmp_path: Path) -> None:
    first = load_project_pack(
        _make_pack(tmp_path / "pack-a", identifier="alpha", source_text="Alpha metric")
    )
    second = load_project_pack(
        _make_pack(tmp_path / "pack-b", identifier="beta", source_text="Beta interface")
    )

    assert first.project.project_id == "project:alpha"
    assert second.project.project_id == "project:beta"
    assert first.sources[0].byte_sha256 != second.sources[0].byte_sha256
    assert "FUTURE_CANARY" not in first.model_dump_json()


def test_loader_rejects_source_path_escape(tmp_path: Path) -> None:
    root = _make_pack(tmp_path / "pack", identifier="escape", source_text="safe")
    manifest_path = root / "source-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[0]["path"] = "../future/outcome-report.md"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ProjectPackError, match="outside the runtime source boundary"):
        load_project_pack(root)


def test_scripted_fixture_requires_explicit_policy_and_flag(tmp_path: Path) -> None:
    root = _make_pack(tmp_path / "pack", identifier="scripted", source_text="safe")
    (root / "scripted").mkdir()
    (root / "scripted" / "model-fixtures.json").write_text("{}", encoding="utf-8")

    load_project_pack(root, include_scripted=False)
    with pytest.raises(ProjectPackError, match="does not allow"):
        load_project_pack(root, include_scripted=True)
