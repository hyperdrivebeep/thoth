"""Freeze model phase ordering and failure boundaries through normal research RPC."""

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_focus_basis import research_host
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup
from tests.integration.test_thread_cycle_preparation_contract import in_transaction

from thoth.application.services.full_project_memory import FullProjectMemoryService
from thoth.application.workflows.thread_cycle import (
    ThreadCycleCommand,
    ThreadCycleResult,
    ThreadCycleService,
)
from thoth.domain.action import ActionPlanDraft
from thoth.domain.canonical import model_digest
from thoth.domain.enums import ModelRole, OperationState, ThreadExecutionState
from thoth.domain.evidence_requirements import HypothesisSemanticReview
from thoth.domain.hypothesis_full import HypothesisRecord
from thoth.domain.memory_preparation import MemoryPreparationBasis
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.research_execution import ResearchWork, research_work
from thoth.ports.model import ModelOutputContractHold


class PhaseModel(ControlledResearchModel):
    def __init__(self, mode: str, events: list[str]) -> None:
        super().__init__(one=True, tested=True)
        self.mode = mode
        self.events = events
        self.actions = 0
        self.blocked = asyncio.Event()
        self.unblock = asyncio.Event()
        self.cancelled = False
        self.owner: ResearchWork | None = None
        self.requests: list[ModelRequest[BaseModel]] = []
        self.draft_refs: list[str] = []

    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        command = request
        role = command.role
        if role in {ModelRole.HYPOTHESIS_REVIEWER, ModelRole.ACTION_PLANNER}:
            assert not in_transaction()
            assert research_work.get() is self.owner and self.owner is not None
            self.requests.append(cast(ModelRequest[BaseModel], command))
            self.events.append("model:" + role.value)
            if role is ModelRole.HYPOTHESIS_REVIEWER:
                assert self.events[-2] == "draft"
                assert command.output_model is HypothesisSemanticReview
                assert command.max_output_tokens == 5000
                assert command.prompt_version == "hypothesis_semantic_review.v5"
                proposed = cast(
                    tuple[HypothesisRecord, ...],
                    command.context_pack.research_context["proposed_full_hypotheses"],
                )
                assert self.draft_refs == [item.revision_digest for item in proposed]
            else:
                self.actions += 1
                assert (
                    command.output_model is ActionPlanDraft and command.max_output_tokens == 12000
                )
                assert command.prompt_version == (
                    "action_alternatives.v3"
                    if self.actions == 1
                    else "action_alternatives.semantic_repair.v3"
                )
                assert (
                    command.context_pack.research_context["hypothesis_review"]
                    == self.owner.context["hypothesis_review"]
                )
                assert (
                    command.context_pack.research_context["hypothesis_revision_refs"]
                    == self.owner.context["hypothesis_revision_refs"]
                    == self.draft_refs
                )
            selected = "review" if role is ModelRole.HYPOTHESIS_REVIEWER else "action"
            if self.mode == selected + "-error":
                raise RuntimeError("fixture model error")
            if self.mode == selected + "-cancel":
                self.blocked.set()
                try:
                    await self.unblock.wait()
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise
        result = await super().structured(command)
        if isinstance(result.output, HypothesisSemanticReview) and self.mode in {
            "coverage-hold",
            "unknown-evidence",
        }:
            review = result.output.model_copy(
                update={
                    "decisions": ()
                    if self.mode == "coverage-hold"
                    else tuple(
                        item.model_copy(update={"evidence_refs": ("span:unknown",)})
                        for item in result.output.decisions
                    )
                }
            )
            return replace(
                result,
                output=command.output_model.model_validate(review),
                output_digest=model_digest("OUTPUT", review, schema_version="1.0.0"),
            )
        if (
            isinstance(result.output, ActionPlanDraft)
            and self.mode in {"repair-success", "repair-failed", "authority-denied"}
            and (self.actions == 1 or self.mode == "repair-failed")
        ):
            draft = result.output.model_copy(update={"proposed_frontier": ("action:unknown",)})
            return replace(
                result,
                output=command.output_model.model_validate(draft),
                output_digest=model_digest("OUTPUT", draft, schema_version="1.0.0"),
            )
        return result


@pytest.mark.parametrize(
    "mode",
    (
        "repair-success",
        "repair-failed",
        "authority-denied",
        "review-error",
        "action-error",
        "review-cancel",
        "action-cancel",
        "coverage-hold",
        "unknown-evidence",
    ),
)
async def test_normal_model_phase_preserves_repair_authority_draft_and_terminal_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    events: list[str] = []
    model = PhaseModel(mode, events)
    runtime = await setup(tmp_path, model)
    execute = ThreadCycleService.execute
    authority = FullProjectMemoryService.require_authority
    raised: list[str] = []
    results: list[ThreadCycleResult] = []

    async def observed_execute(
        service: ThreadCycleService, command: ThreadCycleCommand
    ) -> ThreadCycleResult:
        work = research_work.get()
        assert work is not None
        model.owner = work
        prior = work.show_draft

        def draft(phase: str, payload: dict[str, object]) -> None:
            if phase == "HYPOTHESIS_REVIEW":
                events.append("draft")
                assert payload["state"] == "DRAFT_NOT_COMMITTED"
                model.draft_refs = cast(list[str], payload["proposed_revision_refs"])
                assert len(model.draft_refs) == 1
            if prior is not None:
                prior(phase, payload)

        work.show_draft = draft
        try:
            result = await execute(service, command)
            results.append(result)
            return result
        except asyncio.CancelledError:
            raised.append("CancelledError")
            raise
        except ModelOutputContractHold as error:
            raised.append("ModelOutputContractHold:" + str(error))
            raise
        except ValueError as error:
            raised.append("ValueError:" + str(error))
            raise
        except RuntimeError as error:
            raised.append("RuntimeError:" + str(error))
            raise
        finally:
            assert research_work.get() is work
            work.show_draft = prior

    def require_authority(service: FullProjectMemoryService, basis: MemoryPreparationBasis) -> None:
        if model.actions == 1 and mode in {"repair-success", "repair-failed", "authority-denied"}:
            assert not in_transaction()
            events.append("repair-authority")
            if mode == "authority-denied":
                raise ValueError("fixture authority denied")
        authority(service, basis)

    monkeypatch.setattr(ThreadCycleService, "execute", observed_execute)
    monkeypatch.setattr(FullProjectMemoryService, "require_authority", require_authority)
    try:
        accepted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "phase-contract",
                    {
                        "project_id": "p",
                        "problem": "Compare the connected condition",
                        "contract_version": 2,
                    },
                )
            )
        )
        operation_id, thread_id = accepted["operation_id"], accepted["thread_id"]
        assert isinstance(operation_id, str) and isinstance(thread_id, str)
        if mode.endswith("-cancel"):
            await asyncio.wait_for(model.blocked.wait(), timeout=15)
            cancelled = value(
                await runtime.bus.dispatch(
                    request(
                        "operation/cancel",
                        "cancel-phase",
                        {"project_id": "p", "operation_id": operation_id},
                    )
                )
            )
            assert cancelled["cancelled"] is True
        await asyncio.wait_for(runtime.bus.drain(), timeout=30)
        operation = runtime.bus.read_operation(operation_id)
        assert operation is not None
        expected_state = (
            OperationState.CANCELLED
            if mode.endswith("-cancel")
            else OperationState.SUCCEEDED
            if mode in {"repair-success", "coverage-hold"}
            else OperationState.FAILED
        )
        assert operation.state is expected_state, operation
        thread = research_host(runtime).threads.read(thread_id)
        assert thread is not None and thread.execution_state is ThreadExecutionState.IDLE
        assert events.count("draft") == 1
        expected_roles = [ModelRole.HYPOTHESIS_REVIEWER]
        if mode not in {"review-error", "review-cancel", "coverage-hold", "unknown-evidence"}:
            expected_roles.append(ModelRole.ACTION_PLANNER)
        if mode in {"repair-success", "repair-failed"}:
            expected_roles.append(ModelRole.ACTION_PLANNER)
        assert [item.role for item in model.requests] == expected_roles
        if mode in {"repair-success", "repair-failed", "authority-denied"}:
            assert events.count("repair-authority") == 1
            first = events.index("model:ACTION_PLANNER")
            assert events[first + 1] == "repair-authority"
            if mode != "authority-denied":
                assert events[first + 2] == "model:ACTION_PLANNER"
        if mode == "repair-success":
            assert len(results) == 1 and results[0].semantic_repair_attempted
            assert raised == []
        else:
            assert results == []
            assert not any(
                key.startswith(("HYPOTHESIS:", "ACTION:")) for key in runtime.ledger.read_heads("p")
            )
        if mode.endswith("-cancel"):
            assert model.cancelled and raised == ["CancelledError"]
        if mode == "coverage-hold":
            assert raised == ["ModelOutputContractHold:HYPOTHESIS_REVIEW_COVERAGE_MISMATCH"]
            assert (
                operation.result is not None
                and operation.result["terminal_reason"] == "HYPOTHESIS_REVIEW_COVERAGE_MISMATCH"
            )
        if mode == "unknown-evidence":
            assert raised == ["ValueError:HYPOTHESIS_REVIEW_UNKNOWN_EVIDENCE"]
    finally:
        model.unblock.set()
        await runtime.bus.drain()
        runtime.close()
