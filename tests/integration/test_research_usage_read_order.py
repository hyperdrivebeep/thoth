"""Normal reads retain stored quota and per-operation usage query ordering."""

import asyncio
from collections.abc import Iterable
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup
from tests.integration.test_thread_read_currentness_cost import thread_handlers

from thoth.application.commands.research_threads import ResearchThreadHandlers
from thoth.application.services import research_thread_read_snapshot, research_usage
from thoth.application.services.research_thread_read_snapshot import ResearchThreadReadSnapshot
from thoth.domain.account_usage import AccountQuotaSnapshot
from thoth.domain.model_dispatch import ModelDispatchRecord
from thoth.domain.operation import OperationRecord
from thoth.domain.research_request import ResearchAttempt, ThreadRequestRevision
from thoth.protocol.deferred import PendingExecution


@pytest.mark.parametrize("explicit_quota", [False, True])
@pytest.mark.parametrize("different_operation", [False, True])
async def test_usage_query_order_at_normal_thread_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    explicit_quota: bool,
    different_operation: bool,
) -> None:
    model = ControlledResearchModel()
    runtime = await setup(tmp_path, model, source=True)
    active = False
    worker_patch = pytest.MonkeyPatch()
    worker_entered, worker_release = asyncio.Event(), asyncio.Event()
    try:
        started = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "usage-first",
                    {
                        "project_id": "p",
                        "problem": "LAB-42 지연 조건",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        thread_id, previous_op = str(started["thread_id"]), str(started["operation_id"])
        host = thread_handlers(runtime)
        current_op = previous_op
        if different_operation:
            model.started.clear()
            model.release.clear()
            original_run = host.run

            async def blocked_run(
                attempt: ResearchAttempt,
                request: ThreadRequestRevision,
                operation: OperationRecord,
            ) -> dict[str, JsonValue] | PendingExecution:
                worker_entered.set()
                await worker_release.wait()
                return await original_run(attempt, request, operation)

            worker_patch.setattr(host, "run", blocked_run)
            second = value(
                await runtime.bus.dispatch(
                    request(
                        "thread/input",
                        "usage-second",
                        {
                            "project_id": "p",
                            "thread_id": thread_id,
                            "instruction": "추가 지연 조건",
                            "contract_version": 2,
                        },
                    )
                )
            )
            current_op = str(second["operation_id"])
            await asyncio.wait_for(worker_entered.wait(), timeout=10)
            assert not model.started.is_set()
            assert current_op != previous_op
        events: list[str] = []
        old_quota = host.provider_usage.read_snapshot
        old_operation = host.operations.read
        old_summary = research_usage.summarize_usage
        old_operation_summary = research_usage.summarize_operation_usage
        old_snapshot = research_thread_read_snapshot.read_research_snapshot
        dispatch_objects: list[Iterable[ModelDispatchRecord]] = []

        def checked_snapshot(
            current_host: ResearchThreadHandlers,
            project: str,
            thread: str,
        ) -> ResearchThreadReadSnapshot | None:
            snapshot = old_snapshot(current_host, project, thread)
            assert snapshot is not None and snapshot.manifest is not None
            assert snapshot.manifest.operation_id == previous_op
            assert snapshot.request.operation_id == current_op
            if different_operation:
                assert snapshot.manifest.operation_id and snapshot.request.operation_id
                assert snapshot.manifest.operation_id != snapshot.request.operation_id
            return snapshot

        def label(identifier: str) -> str:
            return (
                "manifest"
                if identifier == previous_op
                else "request"
                if identifier == current_op
                else "other"
            )

        def quota_read(project: str, thread: str) -> AccountQuotaSnapshot:
            nonlocal active
            assert (project, thread) == ("p", thread_id)
            active = True
            events.append("quota.read")
            return old_quota(project, thread)

        def operation_read(identifier: str) -> OperationRecord | None:
            if active:
                events.append("operation.read:" + label(identifier))
            return old_operation(identifier)

        def summary(
            records: Iterable[ModelDispatchRecord],
            thread: str,
            calls: int,
            *,
            account_quota: object | None = None,
        ) -> dict[str, JsonValue]:
            nonlocal active
            active = True
            events.append("usage.summary")
            assert isinstance(records, tuple)
            dispatch_objects.append(records)
            return old_summary(records, thread, calls, account_quota=account_quota)

        def operation_summary(
            records: Iterable[ModelDispatchRecord],
            thread: str,
            operation_id: str,
            *,
            wall_ms: int | None = None,
        ) -> dict[str, JsonValue] | None:
            nonlocal active
            events.append("operation.summary:" + label(operation_id))
            assert records is dispatch_objects[0]
            result = old_operation_summary(records, thread, operation_id, wall_ms=wall_ms)
            if operation_id == current_op:
                active = False
            return result

        def refresh(project: str, thread: str) -> AccountQuotaSnapshot:
            assert (project, thread) == ("p", thread_id)
            return AccountQuotaSnapshot(state="OBSERVED", provider="controlled")

        with monkeypatch.context() as query_patch:
            query_patch.setattr(
                research_thread_read_snapshot, "read_research_snapshot", checked_snapshot
            )
            query_patch.setattr(host.provider_usage, "read_snapshot", quota_read)
            query_patch.setattr(host.provider_usage, "refresh", refresh)
            query_patch.setattr(host.operations, "read", operation_read)
            query_patch.setattr(research_usage, "summarize_usage", summary)
            query_patch.setattr(research_usage, "summarize_operation_usage", operation_summary)
            result = value(
                await runtime.bus.query(
                    request(
                        "thread/read",
                        "usage-read",
                        {
                            "project_id": "p",
                            "thread_id": thread_id,
                            "view": "PROGRESS",
                            "refresh_account_quota": explicit_quota,
                        },
                    )
                )
            )
        expected = [] if explicit_quota else ["quota.read"]
        expected.extend(["usage.summary", "operation.read:manifest", "operation.summary:manifest"])
        if different_operation:
            expected.extend(["operation.read:request", "operation.summary:request"])
        assert events == expected
        assert "result_usage" in result and len(dispatch_objects) == 1
        result_usage = cast(dict[str, JsonValue], result["result_usage"])
        assert previous_op in result_usage
        if different_operation:
            assert current_op not in result_usage
    finally:
        active = False
        worker_patch.undo()
        worker_release.set()
        model.release.set()
        await runtime.bus.drain()
        runtime.close()
