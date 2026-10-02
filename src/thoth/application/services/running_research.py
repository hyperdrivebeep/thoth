"""Whether an investigation is running or waiting in a project, from the operation journal."""

from __future__ import annotations

from thoth.domain.enums import OperationState
from thoth.domain.operation import RESEARCH_OPERATION_METHODS
from thoth.ports.operation import OperationStorePort

_OPEN_STATES = frozenset({OperationState.PENDING, OperationState.RUNNING})


def research_is_running(operations: OperationStorePort, project_id: str) -> bool:
    """An investigation is PENDING once accepted and RUNNING until its result is stored."""

    return any(
        item.state in _OPEN_STATES and item.method in RESEARCH_OPERATION_METHODS
        for item in operations.list_by_project(project_id)
    )
