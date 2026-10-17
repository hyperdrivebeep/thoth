"""Read already-scoped thread and result usage without refreshing provider quota."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import JsonValue

if TYPE_CHECKING:
    from thoth.application.services.provider_usage_service import ProviderUsageService
    from thoth.application.services.research_thread_read_snapshot import ResearchThreadReadSnapshot
    from thoth.domain.account_usage import AccountQuotaSnapshot
    from thoth.ports.operation import OperationStorePort


def read_thread_usage(
    *,
    project_id: str,
    thread_id: str,
    snapshot: ResearchThreadReadSnapshot,
    quota: AccountQuotaSnapshot | None,
    provider_usage: ProviderUsageService,
    operations: OperationStorePort,
) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    from thoth.application.services.research_usage import (
        operation_wall_ms,
        summarize_operation_usage,
        summarize_usage,
    )

    dispatches, budget = snapshot.dispatches, snapshot.budget
    manifest, request = snapshot.manifest, snapshot.request
    usage = summarize_usage(
        tuple(dispatches),
        thread_id,
        0 if budget is None else budget.calls,
        account_quota=(
            quota if quota is not None else provider_usage.read_snapshot(project_id, thread_id)
        ).model_dump(mode="json"),
    )
    # Per-result usage, computed from this read's dispatches: the result's own operation and the
    # one currently requested. A result without recorded calls simply has no entry.
    result_usage: dict[str, JsonValue] = {}
    for usage_op in dict.fromkeys(
        op_id
        for op_id in (None if manifest is None else manifest.operation_id, request.operation_id)
        if op_id
    ):
        usage_record = operations.read(usage_op)
        entry = summarize_operation_usage(
            dispatches,
            thread_id,
            usage_op,
            wall_ms=None
            if usage_record is None
            else operation_wall_ms(usage_record.created_at, usage_record.completed_at),
        )
        if entry is not None:
            result_usage[usage_op] = entry
    return usage, result_usage
