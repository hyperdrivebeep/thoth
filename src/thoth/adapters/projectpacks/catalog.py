from __future__ import annotations

from pathlib import Path

from thoth.adapters.projectpacks.loader import ProjectPackError, load_project_pack
from thoth.domain.projectpack import LoadedProjectPack
from thoth.domain.projectpack_execution import ProjectPackDescriptor
from thoth.ports.projectpack import ProjectPackLoaderPort


class FilesystemProjectPackLoader(ProjectPackLoaderPort):
    def __init__(self, root: Path, *, default_provider: str = "default") -> None:
        self._root = root.resolve()
        self._default_provider = default_provider

    def list(self) -> tuple[ProjectPackDescriptor, ...]:
        if not self._root.is_dir():
            return ()
        values: list[ProjectPackDescriptor] = []
        for child in sorted(self._root.iterdir(), key=lambda path: path.name):
            if not child.is_dir():
                continue
            try:
                pack = load_project_pack(child)
            except ValueError:
                continue
            values.append(
                ProjectPackDescriptor(
                    pack_name=child.name,
                    pack_id=pack.project.pack_id,
                    project_id=pack.project.project_id,
                    name=pack.project.name,
                    problem=pack.scenario.problem,
                    scripted_allowed=pack.policy.allow_scripted_model,
                    default_provider=(
                        "scripted-fixture"
                        if pack.policy.allow_scripted_model
                        else self._default_provider
                    ),
                )
            )
        return tuple(values)

    def load(self, pack_name: str, *, include_scripted: bool) -> LoadedProjectPack:
        path = (self._root / pack_name).resolve()
        if not path.is_relative_to(self._root):
            raise ProjectPackError("ProjectPack path escaped the configured root")
        return load_project_pack(path, include_scripted=include_scripted)
