"""Normal research entry preserves draft projection, head preparation and protected call order."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar, copy_context
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.application.services.full_project_memory import (
    FullMemoryPromotionResult,
    FullProjectMemoryService,
)
from thoth.application.services.revision_service import CommitResult
from thoth.application.workflows.thread_cycle import (
    ThreadCycleCommand,
    ThreadCycleResult,
    ThreadCycleService,
)
from thoth.domain.canonical import model_digest
from thoth.domain.enums import HypothesisStatus, ModelRole, PortfolioStatus
from thoth.domain.evidence_requirements import HypothesisSemanticReview
from thoth.domain.hypothesis import HypothesisPortfolio
from thoth.domain.memory import MemoryRecord
from thoth.domain.memory_preparation import MemoryPreparationBasis
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.revision import StagedRevision
from thoth.ports.model import ModelOutputContractHold

ACTIVE_CYCLE: ContextVar[bool] = ContextVar("test_active_preparation_cycle", default=False)


def in_transaction() -> bool:
    return any(
        item is not None
        for key, item in copy_context().items()
        if key.name == "thoth_sqlite_ambient_transaction"
    )


class ContractModel(ControlledResearchModel):
    def __init__(self, events: list[str], *, one: bool, incomplete_review: bool) -> None:
        super().__init__(one=one, tested=True)
        self.events = events
        self.incomplete_review = incomplete_review
        self.generated: list[HypothesisPortfolio] = []
        self.reviewed: list[HypothesisPortfolio] = []

    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        if ACTIVE_CYCLE.get():
            assert not in_transaction()
            self.events.append("model:" + request.role.value)
        if request.role == ModelRole.HYPOTHESIS_REVIEWER:
            candidate = request.context_pack.candidate_portfolio
            assert candidate is not None
            self.reviewed.append(candidate)
            assert candidate.status is PortfolioStatus.DRAFT
            assert all(
                h.status is HypothesisStatus.DRAFT and h.semantic_review_ref
                for h in candidate.hypotheses
            )
        result = await super().structured(request)
        assert not in_transaction()
        if isinstance(result.output, HypothesisPortfolio):
            promoted = result.output.model_copy(
                update={
                    "status": PortfolioStatus.TESTABLE,
                    "hypotheses": tuple(
                        h.model_copy(
                            update={
                                "status": HypothesisStatus.TESTABLE,
                                "predicted_observations": ("Controlled expected observation",),
                            }
                        )
                        for h in result.output.hypotheses
                    ),
                }
            )
            parsed = request.output_model.model_validate(promoted.model_dump(mode="python"))
            assert isinstance(parsed, HypothesisPortfolio)
            self.generated.append(parsed)
            result = replace(
                result,
                output=parsed,
                output_digest=model_digest("OUTPUT", parsed, schema_version="1.0.0"),
            )
        elif self.incomplete_review and isinstance(result.output, HypothesisSemanticReview):
            rejected = result.output.model_copy(update={"decisions": ()})
            result = replace(
                result,
                output=rejected,
                output_digest=model_digest("OUTPUT", rejected, schema_version="1.0.0"),
            )
        return result


@pytest.mark.parametrize(
    ("one", "incomplete_review"), [(False, False), (True, False), (True, True)]
)
async def test_draft_projection_head_overrides_and_model_authority_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    one: bool,
    incomplete_review: bool,
) -> None:
    events: list[str] = []
    model = ContractModel(events, one=one, incomplete_review=incomplete_review)
    runtime = await setup(tmp_path, model, source=True)
    execute = ThreadCycleService.execute
    authority = FullProjectMemoryService.require_authority
    commit = cast(
        Callable[
            ...,
            Awaitable[
                tuple[CommitResult, tuple[MemoryRecord, ...], FullMemoryPromotionResult | None]
            ],
        ],
        vars(ThreadCycleService)["_commit_cycle"],
    )
    holds: list[str] = []
    results: list[ThreadCycleResult] = []
    expected_records: list[dict[str, str]] = []

    async def observed_execute(
        self: ThreadCycleService, command: ThreadCycleCommand
    ) -> ThreadCycleResult:
        heads = runtime.ledger.read_heads(command.project_id)
        key = next(key for key in heads if key.startswith("THREAD:request:"))
        overrides = {key: heads[key]}
        command = replace(command, expected_head_overrides=overrides)
        expected_records.append(dict(overrides))
        token = ACTIVE_CYCLE.set(True)
        try:
            result = await execute(self, command)
            results.append(result)
            return result
        except ModelOutputContractHold as exc:
            holds.append(str(exc))
            raise
        finally:
            assert command.expected_head_overrides == expected_records[-1]
            ACTIVE_CYCLE.reset(token)

    def require_authority(self: FullProjectMemoryService, basis: MemoryPreparationBasis) -> None:
        if ACTIVE_CYCLE.get():
            events.append("authority:inside" if in_transaction() else "authority:outside")
        authority(self, basis)

    async def observed_commit(self: ThreadCycleService, **kwargs: object):
        events.append("commit_cycle")
        assert not in_transaction()
        command = cast(ThreadCycleCommand, kwargs["command"])
        heads = cast(dict[str, str], kwargs["heads"])
        staged = cast(tuple[StagedRevision, ...], kwargs["staged"])
        expected = cast(dict[str, str], kwargs["expected_heads"])
        keys = {f"{item.revision.entity_type.value}:{item.revision.entity_id}" for item in staged}
        assert set(expected) == (set(heads) & keys) | set(command.expected_head_overrides)
        assert all(
            expected[key] == digest for key, digest in command.expected_head_overrides.items()
        )
        assert expected is not heads and expected is not command.expected_head_overrides
        return await commit(self, **kwargs)

    monkeypatch.setattr(ThreadCycleService, "execute", observed_execute)
    monkeypatch.setattr(ThreadCycleService, "_commit_cycle", observed_commit)
    monkeypatch.setattr(FullProjectMemoryService, "require_authority", require_authority)
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "preparation-contract",
                    {
                        "project_id": "p",
                        "problem": "Compare the connected condition",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        assert len(model.generated) == len(model.reviewed) == 1
        original, candidate = model.generated[0], model.reviewed[0]
        assert len(original.hypotheses) == len(candidate.hypotheses) == int(one)
        assert original.status is PortfolioStatus.TESTABLE
        assert all(h.status is HypothesisStatus.TESTABLE for h in original.hypotheses)
        assert candidate.portfolio_id == original.portfolio_id
        assert candidate.generated_from_head_set == original.generated_from_head_set
        assert candidate.alternatives_considered == original.alternatives_considered
        assert candidate.next_checks == original.next_checks
        assert candidate.uncertainty_reserve == original.uncertainty_reserve
        assert [h.hypothesis_id for h in candidate.hypotheses] == [
            h.hypothesis_id for h in original.hypotheses
        ]
        roles = [item for item in events if item.startswith("model:")]
        expected_roles = ["model:HYPOTHESIS_GENERATOR", "model:HYPOTHESIS_REVIEWER"]
        if not incomplete_review:
            expected_roles.append("model:ACTION_PLANNER")
        assert roles == expected_roles
        assert events.index("model:HYPOTHESIS_GENERATOR") < events.index("authority:outside")
        assert events.index("authority:outside") < events.index("model:HYPOTHESIS_REVIEWER")
        if incomplete_review:
            assert holds == ["HYPOTHESIS_REVIEW_COVERAGE_MISMATCH"]
            assert not results and "commit_cycle" not in events and "authority:inside" not in events
            assert not any(
                k.startswith(("HYPOTHESIS:", "ACTION:")) for k in runtime.ledger.read_heads("p")
            )
        else:
            assert holds == [] and len(results) == 1
            assert events.index("model:ACTION_PLANNER") < events.index("commit_cycle")
            assert events.index("commit_cycle") < events.index("authority:inside")
            assert results[0].model_ids == ("CONTROLLED_RESEARCH", "CONTROLLED_RESEARCH")
            assert results[0].semantic_repair_attempted is False
    finally:
        runtime.close()
