from __future__ import annotations

from typing import Protocol

from thoth.domain.project import Project


class ProjectAlreadyExistsError(ValueError):
    pass


class ProjectStorePort(Protocol):
    def create(self, project: Project, *, created_at: str) -> None: ...

    def read(self, project_id: str) -> Project | None: ...

    def list(self) -> tuple[Project, ...]: ...

    def update(self, project: Project, *, expected_revision: int) -> bool: ...
