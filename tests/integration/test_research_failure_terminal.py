import asyncio
import inspect
from pathlib import Path

import pytest
from tests.atomicity.harness import assert_phase_delta, snapshot
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.application.commands.research_threads import ResearchThreadHandlers
from thoth.application.services.research_boundary import RequestBoundary
from thoth.application.services.research_termination import ResearchTerminationService
from thoth.domain.enums import EntityType
from thoth.domain.research_failure import ResearchFailureRecord, failure_cause
from thoth.domain.research_lease import ResearchLeaseLost
from thoth.domain.research_request import ResearchAttempt, ThreadRequestRevision
from thoth.domain.resource_scope import resource_use_scope
from thoth.ports.model import ModelExecutionHold


@pytest.mark.parametrize("release_fault", [False, True])
async def test_primary_and_publication_failure_are_terminal_and_replayable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, release_fault: bool
) -> None:
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model)
    try:
        handler = runtime.bus._registry.resolve("thread/read")  # pyright: ignore[reportPrivateUsage]
        assert inspect.ismethod(handler) and isinstance(handler.__self__, ResearchThreadHandlers)
        host = handler.__self__
        original = host.publish

        def publish(*args: object, **kwargs: object):
            if args[3] == "HOLD":
                raise ValueError("TEST_PUBLICATION_FAILURE")
            return original(*args, **kwargs)  # type: ignore

        async def failed(*args: object, **kwargs: object):
            raise ModelExecutionHold("MODEL_CALL_TIME_BUDGET_EXHAUSTED")

        monkeypatch.setattr(host, "publish", publish)
        monkeypatch.setattr(model, "structured", failed)
        if release_fault:

            def broken_release(*_: object) -> None:
                raise RuntimeError("LEASE_RELEASE_TEST_FAILURE")

            monkeypatch.setattr(host.leases, "release", broken_release)
        req = request(
            "thread/start",
            "failure",
            {"project_id": "p", "problem": "Question", "contract_version": 2},
        )
        admitted = value(await runtime.bus.dispatch(req))
        await runtime.bus.drain()
        op = runtime.bus.read_operation(str(admitted["operation_id"]))
        assert op is not None and op.state.value == "FAILED" and op.error
        data = op.error["data"]
        assert isinstance(data, dict)
        failure = ResearchFailureRecord.model_validate(data["failure"])
        assert failure.primary.reason_code == "MODEL_CALL_TIME_BUDGET_EXHAUSTED"
        assert failure.secondary is not None and failure.secondary.origin == "HOLD_PUBLICATION"
        status = value(
            await runtime.bus.query(
                request(
                    "thread/read", "read", {"project_id": "p", "thread_id": admitted["thread_id"]}
                )
            )
        )
        assert status["attempt"]["status"] == "FAILED"
        assert status["execution_summary"]["effective_execution_state"] == "FAILED"
        assert bool(status["cleanup_failure"]) is release_fault
        replay = await runtime.bus.dispatch(req)
        assert replay.error is not None and replay.error.model_dump(mode="json") == op.error
        assert len(model.calls) == 0
    finally:
        runtime.close()


async def test_termination_fault_rolls_back_and_fenced_worker_cannot_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model)
    try:
        admitted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "owned",
                    {"project_id": "p", "problem": "latency", "contract_version": 2},
                )
            )
        )
        await asyncio.wait_for(model.started.wait(), 10)
        handler = runtime.bus._registry.resolve("thread/read")  # pyright: ignore[reportPrivateUsage]
        assert inspect.ismethod(handler) and isinstance(handler.__self__, ResearchThreadHandlers)
        host = handler.__self__
        attempt = host.records.journal_read("p", str(admitted["operation_id"]), ResearchAttempt)
        current = host.records.read("p", EntityType.THREAD, f"request:{admitted['thread_id']}")
        assert attempt and current and host.records.events
        req = ThreadRequestRevision.model_validate(current[1])
        service = ResearchTerminationService(host.records, host.operations, host.threads)
        cause = failure_cause(RuntimeError("CONTROLLED_INTERNAL_FAILURE"), "RESEARCH_EXECUTION")
        before = snapshot(runtime.ledger.engine)
        with pytest.raises(ResearchLeaseLost):
            service.finish_failure(
                attempt.model_copy(update={"worker_id": "not-owner"}), req, cause
            )
        assert_phase_delta(before, snapshot(runtime.ledger.engine))
        append = host.records.events.append

        def fault(*args: object, **kwargs: object):
            if kwargs.get("event_type") == "research.failed":
                raise RuntimeError("FAILURE_EVENT_WRITE")
            return append(*args, **kwargs)  # type: ignore

        with monkeypatch.context() as patch:
            patch.setattr(host.records.events, "append", fault)
            with pytest.raises(RuntimeError, match="FAILURE_EVENT_WRITE"), resource_use_scope("p"):
                service.finish_failure(attempt, req, cause)
        assert_phase_delta(before, snapshot(runtime.ledger.engine))
        with resource_use_scope("p"):
            terminal = service.finish_failure(attempt, req, cause)
            after = snapshot(runtime.ledger.engine)
            assert service.finish_failure(attempt, req, cause) == terminal
            assert_phase_delta(after, snapshot(runtime.ledger.engine))
    finally:
        model.release.set()
        await runtime.bus.drain()
        runtime.close()


@pytest.mark.parametrize("transport_reports", [False, True])
async def test_short_deadline_preserves_the_observed_reason_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, transport_reports: bool
) -> None:
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model)
    try:
        expected = (
            "OAUTH_TRANSPORT_DEADLINE_REMOTE_STOP_UNKNOWN"
            if transport_reports
            else "MODEL_CALL_TIME_BUDGET_EXHAUSTED"
        )
        if transport_reports:

            async def deadline(*args: object, **kwargs: object):
                raise ModelExecutionHold(expected)

            monkeypatch.setattr(model, "structured", deadline)
        else:

            def short_deadline(_: RequestBoundary) -> float:
                return 0.01

            monkeypatch.setattr(RequestBoundary, "call_timeout", short_deadline)
        admitted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "deadline",
                    {"project_id": "p", "problem": "latency", "contract_version": 2},
                )
            )
        )
        await runtime.bus.drain()
        status = value(
            await runtime.bus.query(
                request(
                    "thread/read", "status", {"project_id": "p", "thread_id": admitted["thread_id"]}
                )
            )
        )
        assert status["current_result"]["terminal_reason"] == expected
        assert status["current_result"]["completion"] == "TERMINAL"
        assert status["usage"]["total_tokens"] is None
        assert status["budget"]["calls"] == 1
    finally:
        runtime.close()
