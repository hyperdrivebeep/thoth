"""Bring an archived project back to the lifecycle it had before it was archived."""

from __future__ import annotations

from pydantic import JsonValue

from thoth.application.commands.projects import ProjectCommandHandlers, ProjectExpectedRevisionInput
from thoth.domain.enums import ProjectLifecycle
from thoth.domain.project import Project
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.project import ProjectStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class ProjectRestoreHandlers:
    """project/restore: one lifecycle step and one revision, recorded like archive."""

    def __init__(
        self,
        *,
        store: ProjectStorePort,
        governance: GovernanceStorePort,
        ledger: LedgerPort,
        projects: ProjectCommandHandlers,
    ) -> None:
        self._store = store
        self._governance = governance
        self._ledger = ledger
        self._projects = projects

    async def restore(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectExpectedRevisionInput.model_validate(value)
        with self._ledger.transaction():
            project = self._store.read(request.project_id)
            if project is None:
                raise RpcApplicationError(
                    RpcErrorCode.PROJECT_NOT_FOUND,
                    "project was not found",
                    data={"project_id": request.project_id},
                )
            if project.lifecycle == ProjectLifecycle.ARCHIVED_READ_ONLY:
                if project.revision != request.expected_revision:
                    raise RpcApplicationError(
                        RpcErrorCode.DOMAIN_REJECTED,
                        "project revision does not match expected_revision",
                        data={
                            "expected_revision": request.expected_revision,
                            "current_revision": project.revision,
                        },
                    )
                updated = project.model_copy(
                    update={
                        "lifecycle": self._lifecycle_before_archive(project),
                        "revision": project.revision + 1,
                    }
                )
                if not self._store.update(updated, expected_revision=request.expected_revision):
                    raise RpcApplicationError(
                        RpcErrorCode.DOMAIN_REJECTED,
                        "project revision changed concurrently",
                        data={"expected_revision": request.expected_revision},
                    )
        return await self._projects.read({"project_id": request.project_id})

    def _lifecycle_before_archive(self, project: Project) -> ProjectLifecycle:
        """The recorded lifecycle before the archive; if the record cannot say, activation rules."""
        recorded = self._store.prior_lifecycle(project.project_id)
        if recorded is not None:
            return recorded
        active_source = any(
            item.state == "ACTIVE"
            for item in self._governance.list_source_bindings(project.project_id)
        )
        has_policy = self._governance.read_policy(project.project_id) is not None
        return ProjectLifecycle.ACTIVE if active_source and has_policy else ProjectLifecycle.DRAFT
