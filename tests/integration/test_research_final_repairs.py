from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup
from tests.unit.test_post_audit_contracts import span

from thoth.adapters.models.codex_oauth import strict_output_schema
from thoth.adapters.models.reference_schema import constrain_span_references
from thoth.application.services.research_coverage import answer_assessment_state, assess_coverage
from thoth.domain.enums import ModelRole
from thoth.domain.evidence_requirements import (
    EvidenceRequirement,
    RequirementSetRevision,
    ReviewAdjudication,
    ReviewProposal,
    SemanticReviewCandidate,
    SemanticReviewDecision,
)
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.research_request import RevisionRef


def test_applicability_basis_is_an_id_array_in_actual_provider_schema() -> None:
    schema = constrain_span_references(strict_output_schema(ReviewProposal), ("span:one",))
    candidate = cast(dict[str, Any], schema["$defs"])["SemanticReviewCandidate"]
    assert candidate["properties"]["applicability_basis"]["items"]["enum"] == ["span:one"]
    assert candidate["properties"]["evidence_refs"]["items"]["enum"] == ["span:one"]


@pytest.mark.parametrize("required", [False, True])
def test_only_missing_required_scope_can_force_more_search(required: bool) -> None:
    ref = RevisionRef(
        project_id="p",
        entity_type="THREAD",
        entity_id="request",
        revision_id="r",
        revision_digest="a" * 64,
    )
    req = EvidenceRequirement(
        requirement_id="one",
        kind="BOUND_OBLIGATION",
        target="answer",
        rule_refs=("request",),
        question="What does this record say?",
        rationale="Read the given field",
        needed_for="answer",
        blocker="answer:HOLD",
        followup="Read missing requested data",
    )
    requirements = RequirementSetRevision(
        request_ref=ref,
        profile_ref="DOCUMENT_QUESTION:1",
        requirements=(req,),
        mandatory_rule_coverage={"request": "one"},
        generation_run="fixture",
    )
    candidate = ReviewProposal(
        candidates=(
            SemanticReviewCandidate(
                requirement_id="one",
                evidence_refs=("span:one",),
                applicability_basis=("span:one",),
                relation="SUPPORTS",
                applicability="APPLICABLE",
                explanation="The provided record states 12 ms.",
                uncertainty="Not a real-world performance claim.",
                conditions_checked=True,
                time_checked=True,
            ),
        ),
        answer="12 ms in the record",
        unexamined_scope=("The rest of the world was not examined.",),
        missing_required_scope=("A requested field is missing.",) if required else (),
    )
    adjudicated = ReviewAdjudication(
        decisions=(
            SemanticReviewDecision(
                requirement_id="one",
                verdict="APPLIED",
                explanation="Entailed in the requested record scope.",
            ),
        ),
        decomposition_complete=True,
    )
    result, _ = assess_coverage(
        requirements, ref, candidate, adjudicated, (span("span:one", 1, "12 ms"),), "fixture", False
    )
    assert result.web_decision == ("REQUIRED" if required else "SKIPPED_SUFFICIENT")
    assert result.assessments[0].resolution == "SATISFIED"
    optional = result.model_copy(update={"reasons": ("REQUIREMENT_GAPS",)})
    assert answer_assessment_state(optional) == "ASSESSED_WITH_OPEN_CHECKS"
    critical = result.model_copy(update={"gates": {"answer": "HOLD"}})
    assert answer_assessment_state(critical) == "PARTIAL_HOLD"


class ConnectedOnlyModel(ControlledResearchModel):
    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        result = await super().structured(request)
        if request.role == ModelRole.RESEARCH_PLANNER:
            output = request.output_model.model_validate(
                {
                    **result.output.model_dump(),
                    "connected_sources_only": True,
                    "explicit_public_search": False,
                }
            )
            return ModelResult(
                output=output,
                model_id=result.model_id,
                prompt_version=result.prompt_version,
                scripted=True,
                input_digest=result.input_digest,
                output_digest=result.output_digest,
            )
        return result


@pytest.mark.asyncio
async def test_connected_only_is_preserved_even_when_there_are_real_gaps(tmp_path: Path) -> None:
    model = ConnectedOnlyModel(missing=True)
    runtime = await setup(tmp_path, model, source=False)
    try:
        admitted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "scope",
                    {
                        "project_id": "p",
                        "problem": "Use connected records only; do not search the web.",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        state = value(
            await runtime.bus.query(
                request(
                    "thread/read", "read", {"project_id": "p", "thread_id": admitted["thread_id"]}
                )
            )
        )
        assert ModelRole.SOURCE_PLANNER not in [call.role for call in model.calls]
        assert state["current_result"]["result"]["discovery"]["state"] == "SKIPPED_REQUEST_SCOPE"
        assert state["current_result"]["result"]["coverage"]["reasons"]
    finally:
        runtime.close()
