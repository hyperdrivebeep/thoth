from __future__ import annotations

from typing import Protocol

from thoth.domain.investigation import InvestigationAuditRecord, InvestigationRecord


class InvestigationStorePort(Protocol):
    def create(self, value: InvestigationRecord) -> None: ...

    def read(self, investigation_id: str) -> InvestigationRecord | None: ...

    def list(
        self, project_id: str, thread_id: str | None = None
    ) -> tuple[InvestigationRecord, ...]: ...

    def update(self, value: InvestigationRecord, *, expected_plan_revision: int) -> bool: ...

    def append_audit(self, value: InvestigationAuditRecord) -> None: ...

    def list_audit(
        self, project_id: str, investigation_id: str, *, offset: int, limit: int
    ) -> tuple[InvestigationAuditRecord, ...]: ...
