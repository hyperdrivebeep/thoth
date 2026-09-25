from __future__ import annotations

from pathlib import Path
from typing import cast

import yaml

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.criterion_contract import CriterionProfileRecord
from thoth.ports.criterion_profile import CriterionProfileCatalogPort


class FilesystemCriterionProfileCatalog(CriterionProfileCatalogPort):
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def profiles(self) -> tuple[CriterionProfileRecord, ...]:
        if not self._root.is_dir():
            raise ValueError(f"Criterion Profile catalog is unavailable: {self._root}")
        values: list[CriterionProfileRecord] = []
        for path in sorted(self._root.glob("*.yaml"), key=lambda item: item.name):
            resolved = path.resolve()
            if not resolved.is_relative_to(self._root):
                raise ValueError("Criterion Profile path escaped catalog root")
            raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError(f"Criterion Profile must be an object: {path.name}")
            draft = {str(key): child for key, child in cast(dict[object, object], raw).items()}
            values.append(
                CriterionProfileRecord.model_validate(
                    {
                        **draft,
                        "profile_digest": domain_digest(
                            "CRITERION_PROFILE",
                            "1.0.0",
                            canonical_payload(draft),
                        ),
                    }
                )
            )
        refs = [item.profile_ref for item in values]
        if len(refs) != len(set(refs)):
            raise ValueError("Criterion Profile catalog has duplicate profile_ref values")
        if "GENERAL_RND" not in refs:
            raise ValueError("Criterion Profile catalog requires GENERAL_RND")
        return tuple(values)
