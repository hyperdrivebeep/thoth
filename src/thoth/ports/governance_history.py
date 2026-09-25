from typing import Protocol

from thoth.domain.governance_revision import GovernanceKind, GovernanceRevision


class GovernanceHistoryPort(Protocol):
    def read_current(
        self, project_id: str, kind: GovernanceKind, record_id: str
    ) -> GovernanceRevision | None: ...

    def history(
        self, project_id: str, kind: GovernanceKind, record_id: str
    ) -> tuple[GovernanceRevision, ...]: ...
