"""Current-target conflicts gate the same assessment consumed by answers and actions."""

from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup
from tests.unit.test_post_audit_contracts import ref, span

from thoth.adapters.models.codex_oauth import strict_output_schema
from thoth.adapters.models.reference_schema import constrain_span_references
from thoth.adapters.task_profiles import default_task_profiles
from thoth.application.services.requirement_compiler import compile_requirements
from thoth.application.services.research_coverage import answer_assessment_state, assess_coverage
from thoth.domain.enums import ModelRole
from thoth.domain.evidence_requirements import (
    ConflictCandidate,
    ConflictReviewDecision,
    EvidenceRequirement,
    RequirementProposal,
    ReviewAdjudication,
    ReviewProposal,
    SemanticReviewCandidate,
    SemanticReviewDecision,
)
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.research_codec import decode_research_record


@pytest.mark.parametrize(
    "mode,blocked",
    [
        ("current", True),
        ("optional", False),
        ("other-target", False),
        ("unknown-id", True),
        ("stale-resolution", True),
        ("resolved", False),
        ("bad-evidence", True),
    ],
)
def test_compiler_review_and_target_gates(mode: str, blocked: bool) -> None:
    requirements = compile_requirements(
        ref(),
        "What is the current value?",
        RequirementProposal(profile_candidates=("DOCUMENT_QUESTION:1",)),
        (),
        "plan",
        profiles=default_task_profiles(),
    )
    extra = EvidenceRequirement(
        requirement_id="extra",
        kind="RESEARCH_CHECK",
        target="execution",
        question="Optional next experiment",
        rationale="Next phase",
        needed_for="experiment",
        blocker="execution",
        followup="Collect next trial",
    )
    requirements = requirements.model_copy(
        update={"requirements": (*requirements.requirements, extra)}
    )
    target = next(r.requirement_id for r in requirements.requirements if r.target == "answer")
    ids = (
        ("missing",)
        if mode == "unknown-id"
        else ("extra",)
        if mode in {"optional", "other-target"}
        else (target,)
    )
    proposal = ReviewProposal(
        candidates=tuple(
            SemanticReviewCandidate(
                requirement_id=r.requirement_id,
                evidence_refs=("s1",),
                relation="SUPPORTS",
                applicability="APPLICABLE",
                explanation="A12/B20",
                uncertainty="not independently verified",
                conditions_checked=True,
                time_checked=True,
                counterevidence_checked=True,
            )
            for r in requirements.requirements
        ),
        answer="12",
        scoped_conflicts=(
            ConflictCandidate(
                conflict_id="conflict",
                requirement_ids=ids,
                evidence_refs=("bad",) if mode == "bad-evidence" else ("s1",),
                description="A12 and B20 under same condition",
                proposed_relevance="CURRENT_TARGET",
            ),
        ),
    )
    resolution = (
        "NOT_RELEVANT"
        if mode == "optional"
        else "RESOLVED"
        if mode in {"resolved", "stale-resolution"}
        else "UNRESOLVED"
    )
    adjudicated = ReviewAdjudication(
        decisions=tuple(
            SemanticReviewDecision(
                requirement_id=r.requirement_id, verdict="APPLIED", explanation="reviewed"
            )
            for r in requirements.requirements
        ),
        decomposition_complete=True,
        conflict_decisions=(
            ConflictReviewDecision(
                conflict_id="conflict",
                verdict="APPLIED",
                resolution=resolution,
                basis_refs=("s1",),
                explanation="separate review",
                requirement_set_digest="b" * 64
                if mode == "stale-resolution"
                else ref().revision_digest,
            ),
        ),
    )
    coverage, _ = assess_coverage(
        requirements, ref(), proposal, adjudicated, (span("s1", 1, "A12/B20"),), "review", False
    )
    assert (answer_assessment_state(coverage) == "PARTIAL_HOLD") == blocked
    assert (coverage.gates["answer"] == "HOLD") == blocked
    if mode in {"resolved", "optional"}:
        assert coverage.web_decision == "SKIPPED_SUFFICIENT"
        explicit, _ = assess_coverage(
            requirements, ref(), proposal, adjudicated, (span("s1", 1, "A12/B20"),), "review", True
        )
        assert explicit.web_decision == "REQUIRED"
    if mode == "other-target":
        assert coverage.gates["execution"] == "HOLD"
    assert decode_research_record(coverage.model_dump(mode="python")) == coverage


def test_legacy_conflict_is_read_without_rewriting_bytes_or_current_promotion() -> None:
    content: dict[str, object] = {
        "record_kind": "CoverageAssessment",
        "schema_version": "2.0.0",
        "request_ref": ref().model_dump(),
        "requirement_set_ref": ref().model_dump(),
        "assessments": (),
        "web_decision": "REQUIRED",
        "reasons": ("EVIDENCE_CONFLICT",),
        "gates": {"answer": "ASSESSED"},
        "conflicts": ("historical unscoped conflict",),
    }
    decoded = decode_research_record(content)
    assert decoded.model_dump()["schema_version"] == "2.0.0"
    assert "scoped_conflicts" not in decoded.model_dump()
    assert content["gates"] == {"answer": "ASSESSED"}


def test_actual_schema_binds_conflict_ids_to_supplied_requirement_and_evidence() -> None:
    schema = cast(
        dict[str, Any],
        constrain_span_references(
            strict_output_schema(ReviewProposal),
            ("s1",),
            {"requirements": {"requirements": [{"requirement_id": "r1"}]}},
        ),
    )
    fields = schema["$defs"]["ConflictCandidate"]["properties"]
    assert fields["requirement_ids"]["items"]["enum"] == ["r1"]
    assert fields["evidence_refs"]["items"]["enum"] == ["s1"]


class ConflictingModel(ControlledResearchModel):
    def __init__(self, unrelated: bool) -> None:
        super().__init__()
        self.unrelated = unrelated

    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        call = request
        result = await super().structured(call)
        context = call.context_pack.research_context
        output = result.output.model_dump()
        if call.role == ModelRole.SEMANTIC_REVIEWER:
            target = output["candidates"][0]["requirement_id"]
            output["scoped_conflicts"] = [
                ConflictCandidate(
                    conflict_id="normal-conflict",
                    requirement_ids=(target,),
                    evidence_refs=(call.context_pack.evidence[0].span_id,),
                    description="Different beta condition"
                    if self.unrelated
                    else "Same-condition A12/B20",
                    proposed_relevance="OPTIONAL_FOLLOWUP" if self.unrelated else "CURRENT_TARGET",
                ).model_dump()
            ]
        if call.role == ModelRole.REVIEW_ADJUDICATOR:
            basis = cast(dict[str, Any], context["requirement_set_ref"])
            output["conflict_decisions"] = [
                ConflictReviewDecision(
                    conflict_id="normal-conflict",
                    verdict="APPLIED",
                    resolution="NOT_RELEVANT" if self.unrelated else "UNRESOLVED",
                    basis_refs=(call.context_pack.evidence[0].span_id,),
                    requirement_set_digest=basis["revision_digest"],
                    explanation="Independent role checked scope",
                ).model_dump()
            ]
        return ModelResult(
            output=call.output_model.model_validate(output),
            model_id=result.model_id,
            prompt_version=result.prompt_version,
            scripted=True,
            input_digest=result.input_digest,
            output_digest=result.output_digest,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("unrelated", [False, True])
async def test_normal_rpc_consumes_and_persists_the_same_scoped_conflict(
    tmp_path: Path, unrelated: bool
) -> None:
    runtime = await setup(tmp_path, ConflictingModel(unrelated))
    try:
        accepted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "conflict",
                    {
                        "project_id": "p",
                        "contract_version": 2,
                        "problem": "Explain alpha measurement in the connected record.",
                    },
                )
            )
        )
        await runtime.bus.drain()
        operation = runtime.bus.read_operation(str(accepted["operation_id"]))
        assert operation is not None and operation.state.value == "SUCCEEDED"
        result = cast(dict[str, Any], operation.result)
        assert (result["answer_status"] == "PARTIAL_HOLD") is not unrelated
        conflict = result["coverage"]["scoped_conflicts"][0]
        assert conflict["validation"] == "APPLIED"
        assert ("answer" in conflict["blocked_targets"]) is not unrelated
        readback = value(
            await runtime.bus.query(
                request(
                    "thread/read", "read", {"project_id": "p", "thread_id": accepted["thread_id"]}
                )
            )
        )
        assert (
            readback["current_result"]["result"]["coverage"]["scoped_conflicts"]
            == result["coverage"]["scoped_conflicts"]
        )
    finally:
        runtime.close()
