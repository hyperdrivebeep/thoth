from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

import yaml

from thoth.domain.projectpack import (
    LoadedProjectPack,
    PackPolicy,
    PackProject,
    PackScenario,
    PackSource,
)


class ProjectPackError(ValueError):
    pass


def load_project_pack(root: Path, *, include_scripted: bool = False) -> LoadedProjectPack:
    resolved = root.resolve()
    project_document = _yaml_object(resolved / "project.yaml")
    policy_document = _yaml_object(resolved / "policy.yaml")
    scenario_value = project_document.pop("scenario", None)
    if not isinstance(scenario_value, dict):
        raise ProjectPackError("project.yaml requires a scenario object")
    project = PackProject.model_validate(project_document)
    scenario = PackScenario.model_validate(scenario_value)
    policy = PackPolicy.model_validate(policy_document)
    manifest_value = _json_value(resolved / "source-manifest.json")
    if not isinstance(manifest_value, list):
        raise ProjectPackError("source-manifest.json must be an array")
    manifest_items = cast(list[object], manifest_value)
    sources = tuple(PackSource.model_validate(item) for item in manifest_items)
    source_root = (resolved / "sources").resolve()
    for source in sources:
        relative = Path(source.path)
        if relative.is_absolute() or any(
            part in {"..", "future", "oracle"} for part in relative.parts
        ):
            raise ProjectPackError(
                f"source path is outside the runtime source boundary: {source.path}"
            )
        path = (source_root / relative).resolve()
        if not path.is_relative_to(source_root) or not path.is_file():
            raise ProjectPackError(f"source file is missing or outside sources/: {source.path}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != source.byte_sha256.lower():
            raise ProjectPackError(f"source digest mismatch: {source.path}")

    criteria_path = resolved / "criteria.json"
    criteria: tuple[dict[str, object], ...] = ()
    if criteria_path.is_file():
        raw_criteria = _json_value(criteria_path)
        if not isinstance(raw_criteria, list):
            raise ProjectPackError("criteria.json must be an array")
        criteria_items = cast(list[object], raw_criteria)
        criteria = tuple(_string_object(item, "criteria entry") for item in criteria_items)

    scripted: dict[str, object] | None = None
    if include_scripted:
        if not policy.allow_scripted_model:
            raise ProjectPackError("policy does not allow ScriptedModel fixtures")
        scripted = _string_object(
            _json_value(resolved / "scripted" / "model-fixtures.json"),
            "scripted fixture",
        )
    return LoadedProjectPack(
        root=str(resolved),
        project=project,
        scenario=scenario,
        policy=policy,
        sources=sources,
        criteria_templates=criteria,
        scripted_templates=scripted,
    )


def source_path(pack: LoadedProjectPack, source: PackSource) -> Path:
    root = Path(pack.root).resolve()
    source_root = (root / "sources").resolve()
    path = (source_root / source.path).resolve()
    if not path.is_relative_to(source_root):
        raise ProjectPackError("resolved source escaped sources/")
    return path


def _yaml_object(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise ProjectPackError(f"required ProjectPack file is missing: {path.name}")
    loaded = cast(object, yaml.safe_load(path.read_text(encoding="utf-8")))
    return _string_object(loaded, path.name)


def _json_value(path: Path) -> object:
    if not path.is_file():
        raise ProjectPackError(f"required ProjectPack file is missing: {path.name}")
    return cast(object, json.loads(path.read_text(encoding="utf-8")))


def _string_object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProjectPackError(f"{label} must be an object")
    mapping = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in mapping):
        raise ProjectPackError(f"{label} keys must be strings")
    return {str(key): child for key, child in mapping.items()}
