"""Resume an interrupted investigation: completed stages are reused only on the user's request,
only for an exactly equal input, and a reuse is never a model call (T1)."""

from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.apps.runtime import AppRuntime
from thoth.domain.enums import ModelRole
from thoth.domain.evidence_requirements import (
    ConflictCandidate,
    ConflictReviewDecision,
    RequirementSetRevision,
    ReviewAdjudication,
    ReviewProposal,
)
from thoth.domain.model import ModelRequest, ModelResult
from thoth.ports.model import ModelExecutionHold

Json = dict[str, Any]
BEFORE_THE_CUT = (ModelRole.RESEARCH_PLANNER, ModelRole.EVIDENCE_RERANKER)


class CutOffAtReview(ControlledResearchModel):
    """Answers every role but fails the semantic reviewer while 'cut' is set."""

    def __init__(self) -> None:
        super().__init__()
        self.cut = True
        self.by_role: Counter[ModelRole] = Counter()

    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        self.by_role[request.role] += 1
        if self.cut and request.role == ModelRole.SEMANTIC_REVIEWER:
            raise ModelExecutionHold("OAUTH_TRANSPORT_FAILURE")
        return await super().structured(request)


async def _interrupted_run(tmp_path: Path) -> tuple[CutOffAtReview, AppRuntime, Json]:
    model = CutOffAtReview()
    runtime = await setup(tmp_path, model)
    started = value(
        await runtime.bus.dispatch(
            request(
                "thread/start",
                "start",
                {"project_id": "p", "problem": "왜 지연이 생기나요?", "contract_version": 2},
            )
        )
    )
    await runtime.bus.drain()
    return model, runtime, started


async def _read(runtime: AppRuntime, thread_id: str, key: str) -> Json:
    return value(
        await runtime.bus.dispatch(
            request("thread/read", key, {"project_id": "p", "thread_id": thread_id, "view": "FULL"})
        )
    )


def _stages(read: Json) -> list[Json]:
    stages: list[Json] = read["completed_stages"]
    return stages


@pytest.mark.asyncio
async def test_a_resume_reuses_the_finished_stages_and_calls_only_what_was_cut_off(
    tmp_path: Path,
) -> None:
    model, runtime, started = await _interrupted_run(tmp_path)
    try:
        thread = str(started["thread_id"])
        first = await _read(runtime, thread, "read-1")
        assert first["current_result"]["terminal_reason"] == "OAUTH_TRANSPORT_FAILURE"
        done_before = {s["role"] for s in _stages(first)}
        assert {r.value for r in BEFORE_THE_CUT} <= done_before
        model.cut = False
        model.by_role.clear()
        resumed_reply = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "resume",
                    {
                        "project_id": "p",
                        "thread_id": thread,
                        "contract_version": 2,
                        "resume_from_operation_id": started["operation_id"],
                    },
                )
            )
        )
        resumed = resumed_reply
        await runtime.bus.drain()
        operation = runtime.bus.read_operation(str(resumed["operation_id"]))
        assert operation is not None and operation.state.value == "SUCCEEDED", operation
        # the stages that had finished are not called again; the cut-off one and the rest are
        assert all(model.by_role[role] == 0 for role in BEFORE_THE_CUT)
        assert model.by_role[ModelRole.SEMANTIC_REVIEWER] >= 1
        second = await _read(runtime, thread, "read-2")
        stages = _stages(second)
        reused = [s for s in stages if s["reused_from_operation_id"] == started["operation_id"]]
        assert {s["role"] for s in reused} >= {r.value for r in BEFORE_THE_CUT}
        # a reuse is not a model call: it carries no dispatch and is counted apart
        assert all(s["dispatch_ids"] == [] for s in reused)
        counts = second["stage_reuse"]
        assert counts["reused"] == len(reused) and counts["new"] == len(stages) - len(reused)
        assert counts["new"] >= 1
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_without_the_users_resume_nothing_is_reused(tmp_path: Path) -> None:
    model, runtime, started = await _interrupted_run(tmp_path)
    try:
        thread = str(started["thread_id"])
        model.cut = False
        model.by_role.clear()
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "again",
                    {
                        "project_id": "p",
                        "thread_id": thread,
                        "contract_version": 2,
                        "instruction": "같은 질문을 다시",
                    },
                )
            )
        )
        await runtime.bus.drain()
        assert all(model.by_role[role] >= 1 for role in BEFORE_THE_CUT)
        stages = _stages(await _read(runtime, thread, "read-again"))
        assert not any(s["reused_from_operation_id"] for s in stages)
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_stage_whose_input_changed_since_is_called_again(tmp_path: Path) -> None:
    model, runtime, started = await _interrupted_run(tmp_path)
    try:
        thread = str(started["thread_id"])
        # a second source changes what the evidence stages would see
        (tmp_path / "inbox" / "more.html").write_text(
            '<html><head><meta property="article:published_time" '
            'content="2026-09-02T00:00:00Z" /></head><body><h1>Latency addendum</h1>'
            "<p>단위 ms; 조건 alpha</p><p>기록 ID LAB-43: 지연 15 ms</p></body></html>",
            encoding="utf-8",
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "source-2",
                    {
                        "project_id": "p",
                        "relative_path": "more.html",
                        "media_type": "text/html",
                        "authority": "INFORMAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                )
            )
        )
        model.cut = False
        model.by_role.clear()
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "resume-changed",
                    {
                        "project_id": "p",
                        "thread_id": thread,
                        "contract_version": 2,
                        "resume_from_operation_id": started["operation_id"],
                    },
                )
            )
        )
        await runtime.bus.drain()
        # the reranker saw different evidence, so it is asked again
        assert model.by_role[ModelRole.EVIDENCE_RERANKER] >= 1
        stages = _stages(await _read(runtime, thread, "read-changed"))
        reranker = [s for s in stages if s["role"] == ModelRole.EVIDENCE_RERANKER.value]
        assert reranker and not any(s["reused_from_operation_id"] for s in reranker)
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_resume_must_name_the_latest_finished_run_of_the_thread(tmp_path: Path) -> None:
    model, runtime, started = await _interrupted_run(tmp_path)
    try:
        thread = str(started["thread_id"])
        model.cut = False
        wrong = await runtime.bus.dispatch(
            request(
                "thread/input",
                "resume-wrong",
                {
                    "project_id": "p",
                    "thread_id": thread,
                    "contract_version": 2,
                    "resume_from_operation_id": "operation:does-not-exist",
                },
            )
        )
        assert wrong.error is not None and "RESUME_SOURCE_NOT_CURRENT" in wrong.error.message
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_stage_over_a_source_that_is_no_longer_authorized_is_called_again(
    tmp_path: Path,
) -> None:
    model = CutOffAtReview()
    runtime = await setup(tmp_path, model, source=False)
    try:
        (tmp_path / "inbox").mkdir(exist_ok=True)
        (tmp_path / "inbox" / "records.html").write_text(
            '<html><head><meta property="article:published_time" '
            'content="2026-09-01T00:00:00Z" /></head><body>'
            "<h1>Latency report</h1><p>단위 ms; 조건 alpha</p>"
            "<p>기록 ID LAB-42: 지연 12 ms</p></body></html>",
            encoding="utf-8",
        )
        connected = value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "source",
                    {
                        "project_id": "p",
                        "relative_path": "records.html",
                        "media_type": "text/html",
                        "authority": "INFORMAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                )
            )
        )
        started = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start",
                    {"project_id": "p", "problem": "왜 지연이 생기나요?", "contract_version": 2},
                )
            )
        )
        await runtime.bus.drain()
        thread = str(started["thread_id"])
        # the source is withdrawn before the user resumes
        value(
            await runtime.bus.dispatch(
                request(
                    "project/source/disconnect",
                    "revoke",
                    {
                        "project_id": "p",
                        "binding_id": connected["binding"]["binding_id"],
                        "mode": "REVOKE",
                    },
                )
            )
        )
        model.cut = False
        model.by_role.clear()
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "resume-revoked",
                    {
                        "project_id": "p",
                        "thread_id": thread,
                        "contract_version": 2,
                        "resume_from_operation_id": started["operation_id"],
                    },
                )
            )
        )
        await runtime.bus.drain()
        stages = _stages(await _read(runtime, thread, "read-revoked"))
        # whatever saw the withdrawn source is not reused
        saw_source = [s for s in stages if s["role"] == ModelRole.EVIDENCE_RERANKER.value]
        assert not any(s["reused_from_operation_id"] for s in saw_source)
    finally:
        runtime.close()


class ConflictJudgingModel(ControlledResearchModel):
    """Raises one scoped conflict and judges it on the digest of the run's own requirement set.

    The adjudicator copies that digest from its input, as a real one does, so a judgement made in
    one run only holds for the requirement set of that run.
    """

    def __init__(self, cut_role: ModelRole | None = None) -> None:
        super().__init__()
        self.cut_role = cut_role
        self.by_role: Counter[ModelRole] = Counter()

    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        self.by_role[request.role] += 1
        if self.cut_role is not None and request.role == self.cut_role:
            raise ModelExecutionHold("OAUTH_TRANSPORT_FAILURE")
        result = await super().structured(request)
        context = request.context_pack
        spans = tuple(s.span_id for s in context.evidence)
        if request.role == ModelRole.SEMANTIC_REVIEWER:
            requirements = RequirementSetRevision.model_validate(
                context.research_context["requirements"]
            ).requirements
            conflict = ConflictCandidate(
                conflict_id="conflict-1",
                requirement_ids=(requirements[0].requirement_id,),
                evidence_refs=spans,
                description="두 자료의 지연 값이 다르다",
                proposed_relevance="CURRENT_TARGET",
            )
            output = cast(ReviewProposal, result.output).model_copy(
                update={"scoped_conflicts": (conflict,)}
            )
            return replace(result, output=output)  # type: ignore[arg-type]
        if request.role == ModelRole.REVIEW_ADJUDICATOR:
            reference = cast(dict[str, str], context.research_context["requirement_set_ref"])
            digest = reference["revision_digest"]
            decision = ConflictReviewDecision(
                conflict_id="conflict-1",
                verdict="APPLIED",
                resolution="RESOLVED",
                basis_refs=spans,
                requirement_set_digest=digest,
                explanation="조건이 달라 두 값이 함께 성립한다",
            )
            output = cast(ReviewAdjudication, result.output).model_copy(
                update={"conflict_decisions": (decision,)}
            )
            return replace(result, output=output)  # type: ignore[arg-type]
        return result


def _find(node: object, key: str) -> list[Json] | None:
    """The first list stored under key anywhere in a nested read."""
    children: list[object] = []
    if isinstance(node, dict):
        mapping = cast(Json, node)
        if key in mapping:
            return cast(list[Json], mapping[key])
        children = list(mapping.values())
    elif isinstance(node, list):
        children = list(cast(list[object], node))
    for child in children:
        found = _find(child, key)
        if found is not None:
            return found
    return None


def _conflict_judgements(read: Json) -> list[tuple[str, str, str]]:
    found = _find(read["current_result"], "scoped_conflicts") or []
    return [(c["conflict_id"], c["validation"], c["resolution"]) for c in found]


async def _drive(root: Path, model: ConflictJudgingModel, key: str) -> tuple[AppRuntime, Json, str]:
    runtime = await setup(root, model)
    started = value(
        await runtime.bus.dispatch(
            request(
                "thread/start",
                key,
                {"project_id": "p", "problem": "왜 지연이 생기나요?", "contract_version": 2},
            )
        )
    )
    await runtime.bus.drain()
    return runtime, started, str(started["thread_id"])


@pytest.mark.asyncio
async def test_resuming_after_the_adjudicator_asks_it_again_and_keeps_its_judgement(
    tmp_path: Path,
) -> None:
    # What an uninterrupted run judges is the reference.
    plain = ConflictJudgingModel()
    base_runtime, _, base_thread = await _drive(tmp_path / "base", plain, "start-base")
    try:
        reference = _conflict_judgements(await _read(base_runtime, base_thread, "read-base"))
    finally:
        base_runtime.close()
    assert reference == [("conflict-1", "APPLIED", "RESOLVED")]

    # The same run cut off at the first role after the adjudicator, then resumed by the user.
    model = ConflictJudgingModel(cut_role=ModelRole.HYPOTHESIS_GENERATOR)
    runtime, started, thread = await _drive(tmp_path / "cut", model, "start-cut")
    try:
        done = {s["role"] for s in _stages(await _read(runtime, thread, "read-cut"))}
        assert ModelRole.REVIEW_ADJUDICATOR.value in done
        model.cut_role = None
        model.by_role.clear()
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "resume-judged",
                    {
                        "project_id": "p",
                        "thread_id": thread,
                        "contract_version": 2,
                        "resume_from_operation_id": started["operation_id"],
                    },
                )
            )
        )
        await runtime.bus.drain()
        resumed = await _read(runtime, thread, "read-resumed")
        # the judgement names this run's requirement set, so it is asked again, not reused
        assert model.by_role[ModelRole.REVIEW_ADJUDICATOR] >= 1
        stages = _stages(resumed)
        reused_roles = {s["role"] for s in stages if s["reused_from_operation_id"]}
        # the stages before it are still reused; only the judgement review is asked again
        assert {r.value for r in BEFORE_THE_CUT} | {
            ModelRole.SEMANTIC_REVIEWER.value
        } <= reused_roles
        assert ModelRole.REVIEW_ADJUDICATOR.value not in reused_roles
        assert _conflict_judgements(resumed) == reference
    finally:
        runtime.close()
