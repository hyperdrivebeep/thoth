"""An investigation counts as running from the moment its operation is accepted."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from thoth.application.services.running_research import research_is_running
from thoth.domain.enums import OperationState
from thoth.domain.operation import OperationRecord


def operation(method: str, state: OperationState, project: str = "p") -> OperationRecord:
    return OperationRecord(
        operation_id=f"operation:{method.replace(chr(47), chr(45))}:{state.value}:{project}",
        project_id=project,
        method=method,
        idempotency_key="k",
        scope_digest="a" * 64,
        state=state,
        created_at=datetime(2026, 9, 30, tzinfo=UTC),
    )


class Operations:
    def __init__(self, *items: OperationRecord) -> None:
        self.items = items

    def list_by_project(self, project_id: str) -> tuple[OperationRecord, ...]:
        return tuple(item for item in self.items if item.project_id == project_id)


@pytest.mark.parametrize("state", [OperationState.PENDING, OperationState.RUNNING])
@pytest.mark.parametrize("method", ["thread/start", "thread/input", "thread/steer"])
def test_an_accepted_or_running_investigation_counts(method: str, state: OperationState) -> None:
    assert research_is_running(Operations(operation(method, state)), "p")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "item",
    [
        operation("thread/input", OperationState.SUCCEEDED),
        operation("thread/input", OperationState.FAILED),
        operation("thread/input", OperationState.CANCELLED),
        operation("memory/edit/propose", OperationState.RUNNING),
        operation("thread/input", OperationState.PENDING, project="other"),
    ],
)
def test_finished_other_and_foreign_operations_do_not_count(item: OperationRecord) -> None:
    assert not research_is_running(Operations(item), "p")  # type: ignore[arg-type]
