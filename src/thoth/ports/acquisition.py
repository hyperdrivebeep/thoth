from __future__ import annotations

from typing import Protocol

from thoth.domain.acquisition import (
    AcquisitionCommit,
    AcquisitionLead,
    EvidenceAtomicCommit,
    SearchIntent,
)


class AcquisitionConflictError(RuntimeError):
    pass


class AcquisitionUnitOfWorkPort(Protocol):
    def commit(self, value: AcquisitionCommit) -> None: ...


class EvidenceUnitOfWorkPort(Protocol):
    def commit(self, value: EvidenceAtomicCommit) -> None: ...


class AcquisitionTraceStorePort(Protocol):
    def put_search_intent(self, value: SearchIntent) -> None: ...

    def read_search_intent(self, search_intent_id: str) -> SearchIntent | None: ...

    def list_search_intents(self, investigation_id: str) -> tuple[SearchIntent, ...]: ...

    def list_leads(self, investigation_id: str) -> tuple[AcquisitionLead, ...]: ...
