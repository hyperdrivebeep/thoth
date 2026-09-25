"""Persistence contract for v2 inputs waiting behind a running request."""

from typing import Protocol

from thoth.domain.research_queue import QueuedResearchInput


class ResearchQueueStorePort(Protocol):
    def read(self, project_id: str, operation_id: str) -> QueuedResearchInput | None: ...

    def list(self, project_id: str, thread_id: str) -> tuple[QueuedResearchInput, ...]: ...

    def list_project(self, project_id: str) -> tuple[QueuedResearchInput, ...]: ...

    def save(self, item: QueuedResearchInput) -> None: ...
