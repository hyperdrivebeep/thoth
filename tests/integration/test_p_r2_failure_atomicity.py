"""Prepared R2 failures publish hold/reject and final completion/candidate atomically."""

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pytest
from tests.atomicity.harness import snapshot
from tests.integration.p_r2_failure_helpers import ambient, capture_exit, revise_hypothesis, save
from tests.integration.test_a02_autonomous_acquisition import StaticModelResolver
from tests.integration.test_a04_r2_closed_loop import A04R2Model
from tests.integration.test_hypothesis_prediction_outcome_cycle import (
    MeasurementSandbox,
    prepare_measurement,
    request,
    value,
)

from thoth.application.services.r2_closed_loop import R2ClosedLoopCoordinator
from thoth.application.services.r2_test_lifecycle import R2TestLifecycle
from thoth.application.services.sandbox_service import SandboxService
from thoth.apps.runtime import create_runtime
from thoth.domain.sandbox import SandboxErrorCode, SandboxExecutionState, SandboxFailure
from thoth.ports.model import ModelPort


@pytest.mark.parametrize(
    "path", ["stale", "accept_success", "accept_failure", "assessment", "finalization"]
)
@pytest.mark.parametrize("after_write", [False, True])
@pytest.mark.parametrize("write_number", [1, 2])
async def test_prepared_rejection_pair_rolls_back_on_write_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str, after_write: bool, write_number: int
) -> None:
    states = (SandboxExecutionState.FAILED,) if path == "accept_failure" else ()
    runtime, sandbox, project, thread, _ = await prepare_measurement(
        tmp_path, monkeypatch, states=states
    )
    evidence: dict[str, Any] = {"path": path, "trace": [], "faults": 0, "await_ambient": []}
    originals = {"hold": R2TestLifecycle.hold, "reject": SandboxService.reject_observation}

    async def during_run(spec: Any) -> None:
        evidence["await_ambient"].append(ambient())
        if path == "stale":
            await revise_hypothesis(runtime, project, spec)

    sandbox.on_result = during_run

    def pair_call(name: str, instance: Any, *args: Any, **kwargs: Any) -> Any:
        if not evidence["trace"]:
            evidence["before_phase"] = snapshot(runtime.ledger.engine)
        evidence["trace"].append(name)
        target = len(evidence["trace"]) == write_number
        if target and not after_write:
            evidence["faults"] += 1
            raise RuntimeError("P2 second pair write fault")
        result = originals[name](instance, *args, **kwargs)
        if target:
            evidence["faults"] += 1
            raise RuntimeError("P2 second pair write fault")
        return result

    def hold(instance: Any, *args: Any, **kwargs: Any) -> Any:
        return pair_call("hold", instance, *args, **kwargs)

    def reject(instance: Any, *args: Any, **kwargs: Any) -> Any:
        return pair_call("reject", instance, *args, **kwargs)

    original_basis = SandboxService._validate_admission_basis  # pyright: ignore[reportPrivateUsage]

    def basis(instance: Any, *args: Any, **kwargs: Any) -> Any:
        if sandbox.seen_specs:
            raise SandboxFailure(SandboxErrorCode.INPUT_INVALID, "P2 admission changed")
        return original_basis(instance, *args, **kwargs)

    def invalid_assessment(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("RESEARCH_TEST_ASSESSMENT_REJECTED")

    def invalid_completion(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("P2 finalization failed")

    try:
        command = request("thread/input", "p2-pair", {"project_id": project, "thread_id": thread})
        with monkeypatch.context() as patch:
            capture_exit(patch, runtime, evidence)
            patch.setattr(R2TestLifecycle, "hold", hold)
            patch.setattr(SandboxService, "reject_observation", reject)
            if path.startswith("accept_"):
                patch.setattr(SandboxService, "_validate_admission_basis", basis)
            elif path == "assessment":
                patch.setattr(R2TestLifecycle, "prepare_assessments", invalid_assessment)
            elif path == "finalization":
                patch.setattr(R2TestLifecycle, "complete", invalid_completion)
            response = await runtime.bus.dispatch(command)
        evidence["response"] = response.model_dump(mode="json")
        save(f"pair-{path}-{write_number}-{after_write}", evidence)
        assert evidence["faults"] == 1
        expected = ["hold", "reject"] if path == "stale" else ["reject", "hold"]
        assert evidence["trace"] == expected[:write_number]
        assert evidence["await_ambient"] == [False]
        assert evidence["ambient_after_phase"] is False
        assert response.error is not None
        assert evidence["after_phase"] == evidence["before_phase"]
        terminal = snapshot(runtime.ledger.engine)
        replay = await runtime.bus.dispatch(command)
        assert replay.error == response.error
        assert snapshot(runtime.ledger.engine) == terminal
        assert len(sandbox.seen_specs) == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("state", [SandboxExecutionState.FAILED, SandboxExecutionState.TIMED_OUT])
@pytest.mark.parametrize("after_candidate", [False, True])
@pytest.mark.parametrize("sandbox_error", [False, True])
async def test_final_prepared_completion_and_candidate_roll_back_together(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: SandboxExecutionState,
    after_candidate: bool,
    sandbox_error: bool,
) -> None:
    runtime, sandbox, project, thread, _ = await prepare_measurement(
        tmp_path, monkeypatch, states=(state,), max_transient_retries=0
    )
    evidence: dict[str, Any] = {"complete_hits": 0, "candidate_hits": 0, "await_ambient": []}
    original_accept = SandboxService.accepting_observation
    original_complete = R2TestLifecycle.complete
    original_failure = R2ClosedLoopCoordinator._failure_execution  # pyright: ignore[reportPrivateUsage]

    async def during_run(spec: Any) -> None:
        evidence["await_ambient"].append(ambient())

    sandbox.on_result = during_run

    @contextmanager
    def accept(instance: Any, spec: Any, bundle: Any, **kwargs: Any) -> Generator[Any]:
        evidence["before_phase"] = snapshot(runtime.ledger.engine)
        with original_accept(instance, spec, bundle, **kwargs) as artifact:
            yield artifact

    def complete(instance: Any, *args: Any, **kwargs: Any) -> Any:
        evidence["complete_hits"] += 1
        return original_complete(instance, *args, **kwargs)

    def failure(instance: Any, **kwargs: Any) -> Any:
        evidence["candidate_hits"] += 1
        evidence["has_test_completion"] = kwargs.get("test_completion") is not None
        if after_candidate:
            result = original_failure(instance, **kwargs)
            evidence["candidate_projection"] = result.projection
        if sandbox_error:
            raise SandboxFailure(
                SandboxErrorCode.INPUT_INVALID, "P2 candidate error, not admission"
            )
        raise RuntimeError("P2 candidate publication fault")

    try:
        command = request("thread/input", "p2-final", {"project_id": project, "thread_id": thread})
        with monkeypatch.context() as patch:
            capture_exit(patch, runtime, evidence)
            patch.setattr(SandboxService, "accepting_observation", accept)
            patch.setattr(R2TestLifecycle, "complete", complete)
            patch.setattr(R2ClosedLoopCoordinator, "_failure_execution", failure)
            response = await runtime.bus.dispatch(command)
        evidence["response"] = response.model_dump(mode="json")
        save(f"final-{state.value}-{after_candidate}-{sandbox_error}", evidence)
        assert evidence["complete_hits"] == evidence["candidate_hits"] == 1
        assert evidence["has_test_completion"] is True
        assert evidence["await_ambient"] == [False]
        assert evidence["ambient_after_phase"] is False
        assert response.error is not None
        assert evidence["after_phase"] == evidence["before_phase"]
        terminal = snapshot(runtime.ledger.engine)
        replay = await runtime.bus.dispatch(command)
        assert replay.error == response.error
        assert snapshot(runtime.ledger.engine) == terminal
        assert len(sandbox.seen_specs) == 1
        runtime.close()
        runtime = create_runtime(
            tmp_path / "allowed",
            sandbox_adapter=sandbox,
            model_resolver=StaticModelResolver(cast(ModelPort, A04R2Model())),
        )
        assert snapshot(runtime.ledger.engine) == terminal
        replay = await runtime.bus.dispatch(command)
        assert replay.error == response.error
        assert snapshot(runtime.ledger.engine) == terminal
        again = value(
            await runtime.bus.dispatch(
                request("thread/input", "p2-new-key", {"project_id": project, "thread_id": thread})
            )
        )["r2_closed_loop"]
        assert again["reason_code"] == "RESEARCH_TEST_RESULT_RECONCILIATION_REQUIRED"
        assert again["pending_result"]["exits"] == ["CLEAR_AND_RERUN"]
        assert len(sandbox.seen_specs) == 1
    finally:
        runtime.close()


async def test_retry_awaits_stay_outside_transactions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, sandbox, project, thread, _ = await prepare_measurement(
        tmp_path,
        monkeypatch,
        states=(SandboxExecutionState.TIMED_OUT, SandboxExecutionState.SUCCEEDED),
        max_transient_retries=1,
    )
    original = MeasurementSandbox.run
    boundaries: list[tuple[str, bool]] = []

    async def run(adapter: MeasurementSandbox, spec: Any) -> Any:
        boundaries.append(("before", ambient()))
        result = await original(adapter, spec)
        boundaries.append(("after", ambient()))
        return result

    try:
        monkeypatch.setattr(MeasurementSandbox, "run", run)
        result = value(
            await runtime.bus.dispatch(
                request("thread/input", "p2-retry", {"project_id": project, "thread_id": thread})
            )
        )
        assert result["r2_closed_loop"]["terminal_state"] == "COMPLETED"
        assert boundaries == [("before", False), ("after", False)] * 2
        assert len(sandbox.seen_specs) == 2
    finally:
        runtime.close()
