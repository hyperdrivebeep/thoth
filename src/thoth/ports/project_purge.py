"""Permanent removal of one project's rows and its project-only files."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from thoth.domain.project_deletion import ProjectDeletionRecord


@dataclass(frozen=True)
class PurgeResult:
    record: ProjectDeletionRecord
    deleted_rows: int


class ProjectPurgeBlocked(RuntimeError):
    """A condition in the stored data forbids the purge; nothing was changed."""

    def __init__(self, reason_code: str, *, project_ids: tuple[str, ...] = ()) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.project_ids = project_ids


class ProjectPurgePort(Protocol):
    def read_record(self, project_id: str) -> ProjectDeletionRecord | None:
        """The deletion record of an already deleted project."""
        ...

    def purge(self, record: ProjectDeletionRecord) -> PurgeResult:
        """Delete every row of the project and write the record, inside the caller's transaction.

        The stored record names the project-only folders still to remove. Raises
        ProjectPurgeBlocked before changing anything when another project references this one or
        work for it is still running.
        """
        ...

    def clean_files(self, project_id: str) -> tuple[str, ...]:
        """Remove the project-only upload folder after the commit; returns what is still owed."""
        ...
