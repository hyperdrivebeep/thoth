from __future__ import annotations

from pathlib import Path
from typing import ClassVar, cast

import orjson
import yaml

from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.ports.behavior_artifact import BehaviorArtifactCatalogPort


class FilesystemBehaviorArtifactCatalog(BehaviorArtifactCatalogPort):
    _FILES: ClassVar[dict[BehaviorArtifactKind, Path]] = {
        BehaviorArtifactKind.PROMPT_BUNDLE: Path("prompts/default.yaml"),
        BehaviorArtifactKind.RETRIEVAL_POLICY: Path("retrieval/default.yaml"),
        BehaviorArtifactKind.WORKFLOW_DEFINITION: Path("workflows/default.json"),
        BehaviorArtifactKind.EVALUATOR_CONTRACT: Path("evaluators/default.yaml"),
    }

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def baseline_content(self, kind: BehaviorArtifactKind) -> dict[str, object]:
        relative = self._FILES.get(kind)
        if relative is None:
            return {"behavior_contract": kind.value, "revision": 1}
        path = (self._root / relative).resolve()
        if not path.is_relative_to(self._root):
            raise ValueError("behavior artifact path escaped catalog root")
        raw: object
        if path.suffix == ".json":
            raw = orjson.loads(path.read_bytes())
        else:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"behavior artifact must be an object: {relative.as_posix()}")
        content = {str(key): child for key, child in cast(dict[object, object], raw).items()}
        if content.get("kind") != kind.value:
            raise ValueError(f"behavior artifact kind mismatch: {relative.as_posix()}")
        version = content.get("version")
        if not isinstance(version, str) or not version.strip():
            raise ValueError(f"behavior artifact version is required: {relative.as_posix()}")
        return content
