"""Publication pre-write guards and the normal unchecked HOLD entry remain mandatory."""

import asyncio
from pathlib import Path

import pytest
from pydantic import BaseModel, JsonValue
from tests.atomicity.harness import assert_phase_delta, snapshot
from tests.integration.storage_coverage_helpers import request as rpc
from tests.integration.storage_coverage_helpers import value
from tests.integration.test_research_focus_basis import research_host
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.domain.enums import EntityType, OperationState
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.operation import OperationRecord
from thoth.domain.research_execution import ResearchFence, ResearchWork
from thoth.domain.research_lease import ResearchLeaseLost
from thoth.domain.research_request import ResearchAttempt, RevisionRef, ThreadRequestRevision
from thoth.domain.resource_scope import ResourceScopeError
from thoth.domain.revision import StagedRevision
from thoth.ports.model import ModelExecutionHold
from thoth.protocol.deferred import PendingExecution


@pytest.mark.parametrize("guard", ("boundary", "lease", "request-head", "operation", "access"))
async def test_publication_guard_rejects_before_writes_and_keeps_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, guard: str
) -> None:
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model)
    host = research_host(runtime)
    original = host.publish
    captured: list[tuple[ResearchAttempt, ThreadRequestRevision, ResearchWork]] = []

    def observe(
        attempt: ResearchAttempt,
        request: ThreadRequestRevision,
        work: ResearchWork,
        phase: str,
        result: dict[str, object],
        gaps: tuple[str, ...],
        terminal: str | None = None,
        check: bool = True,
    ) -> RevisionRef:
        captured.append((attempt, request, work))
        return original(attempt, request, work, phase, result, gaps, terminal, check)

    monkeypatch.setattr(host, "publish", observe)
    try:
        value(
            await runtime.bus.dispatch(
                rpc(
                    "thread/start",
                    "guard-source",
                    {"project_id": "p", "problem": "Question", "contract_version": 2},
                )
            )
        )
        await asyncio.wait_for(model.started.wait(), 10)
        attempt, request, work = captured[-1]
        pending = work.pending_revisions
        pending_values = tuple(pending)
        before = snapshot(runtime.ledger.engine)
        writes: list[str] = []

        def forbidden(_staged: tuple[StagedRevision, ...]) -> None:
            writes.append("commit_staged")
            pytest.fail("a refused publication reached its first write")

        def boundary() -> None:
            raise ResearchFence("FIXTURE_BOUNDARY_REJECTED")

        def access(_operation: OperationRecord) -> None:
            raise ResourceScopeError("FIXTURE_ACCESS_DENIED")

        def missing_operation(_identifier: str) -> OperationRecord | None:
            return None

        with monkeypatch.context() as patch:
            patch.setattr(host.records, "commit_staged", forbidden)
            if guard == "boundary":
                patch.setattr(work.boundary, "check", boundary)
            elif guard == "lease":
                patch.setattr(work.boundary, "owns_attempt", lambda: False)
            elif guard == "request-head":
                attempt = attempt.model_copy(
                    update={
                        "request_ref": attempt.request_ref.model_copy(
                            update={"revision_digest": "f" * 64}
                        )
                    }
                )
            elif guard == "operation":
                patch.setattr(host.operations, "read", missing_operation)
            else:
                patch.setattr(host.access, "require_operation", access)
            exception, message = {
                "boundary": (ResearchFence, "FIXTURE_BOUNDARY_REJECTED"),
                "lease": (ResearchLeaseLost, "ATTEMPT_LEASE_FENCED"),
                "request-head": (ResearchFence, "STALE_PUBLICATION"),
                "operation": (ResearchFence, "OPERATION_MISSING"),
                "access": (ResourceScopeError, "FIXTURE_ACCESS_DENIED"),
            }[guard]
            with pytest.raises(exception, match=message):
                original(attempt, request, work, "GUARD", {}, (), check=guard == "boundary")
        assert writes == [] and work.pending_revisions is pending
        assert tuple(work.pending_revisions) == pending_values
        assert_phase_delta(before, snapshot(runtime.ledger.engine))
    finally:
        model.release.set()
        await runtime.bus.drain()
        runtime.close()


class HoldingModel(ControlledResearchModel):
    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        raise ModelExecutionHold("FIXTURE_MODEL_HOLD")


@pytest.mark.parametrize("lost", (False, True))
async def test_normal_hold_skips_only_boundary_check_and_fences_lost_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lost: bool
) -> None:
    runtime = await setup(tmp_path, HoldingModel())
    host = research_host(runtime)
    original_publish, original_run = host.publish, host.run
    trace: list[str] = []
    outcomes: list[dict[str, JsonValue] | PendingExecution] = []

    async def run(
        attempt: ResearchAttempt, request: ThreadRequestRevision, operation: OperationRecord
    ) -> dict[str, JsonValue] | PendingExecution:
        result = await original_run(attempt, request, operation)
        outcomes.append(result)
        return result

    def publish(
        attempt: ResearchAttempt,
        request: ThreadRequestRevision,
        work: ResearchWork,
        phase: str,
        result: dict[str, object],
        gaps: tuple[str, ...],
        terminal: str | None = None,
        check: bool = True,
    ) -> RevisionRef:
        if phase != "HOLD":
            return original_publish(attempt, request, work, phase, result, gaps, terminal, check)
        assert check is False
        owns, read, require = (
            work.boundary.owns_attempt,
            host.records.read,
            host.access.require_operation,
        )

        def no_check() -> None:
            pytest.fail("check=False still ran the boundary check")

        def owned() -> bool:
            trace.append("owns")
            return False if lost else owns()

        def head(
            project: str, entity: EntityType, identifier: str
        ) -> tuple[RevisionRef, dict[str, object]] | None:
            if entity is EntityType.THREAD and identifier == "request:" + request.thread_id:
                trace.append("head")
            return read(project, entity, identifier)

        def access(operation: OperationRecord) -> None:
            trace.append("access")
            require(operation)

        with monkeypatch.context() as patch:
            patch.setattr(work.boundary, "check", no_check)
            patch.setattr(work.boundary, "owns_attempt", owned)
            patch.setattr(host.records, "read", head)
            patch.setattr(host.access, "require_operation", access)
            return original_publish(attempt, request, work, phase, result, gaps, terminal, check)

    monkeypatch.setattr(host, "publish", publish)
    monkeypatch.setattr(host, "run", run)
    try:
        accepted = value(
            await runtime.bus.dispatch(
                rpc(
                    "thread/start",
                    "hold-guard",
                    {"project_id": "p", "problem": "Question", "contract_version": 2},
                )
            )
        )
        await runtime.bus.drain()
        identifier = accepted["operation_id"]
        assert isinstance(identifier, str)
        operation = runtime.bus.read_operation(identifier)
        assert operation is not None and len(outcomes) == 1
        if lost:
            assert trace == ["owns"]
            assert isinstance(outcomes[0], PendingExecution) and outcomes[0].value == {
                "status": "ATTEMPT_FENCED"
            }
            assert operation.state is OperationState.RUNNING
        else:
            assert trace[:3] == ["owns", "head", "access"]
            assert trace.count("owns") == trace.count("head") == trace.count("access") == 1
            assert operation.state is OperationState.SUCCEEDED
            assert (
                operation.result is not None
                and operation.result["terminal_reason"] == "FIXTURE_MODEL_HOLD"
            )
    finally:
        runtime.close()
