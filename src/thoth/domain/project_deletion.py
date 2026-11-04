"""What stays behind after a project is permanently deleted."""

from __future__ import annotations

import hashlib

from pydantic import AwareDatetime

from thoth.domain.base import DomainModel
from thoth.domain.ids import ProjectId, Sha256


def project_name_digest(name: str) -> str:
    """The name is not kept; only a digest that proves a repeated request names the same project."""
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


class ProjectDeletionRecord(DomainModel):
    project_id: ProjectId
    name_sha256: Sha256
    deleted_at: AwareDatetime
    # Workspace-relative paths whose removal is still owed; empty once every cleanup finished.
    cleanup_pending: tuple[str, ...] = ()
