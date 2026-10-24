"""Exercise the real wrapper chain with a network-free model; failed calls leave no lineage."""

from asyncio import CancelledError
from dataclasses import replace
from typing import cast

import pytest
from tests.fixtures.hypothesis_grounding_wire import (
    RecordingGroundingModel,
    generator_request,
    research_fixture,
)

from thoth.application.services.behavior_context import BehaviorWorkContext, behavior_work_scope
from thoth.application.services.behavior_models import BehaviorBoundModel
from thoth.application.services.hypothesis_grounding_hold import GroundingPublicationHold
from thoth.application.services.hypothesis_table_filler import accepted_portfolio
from thoth.application.services.research_models import ResearchModel
from thoth.domain.behavior_execution import ActiveBehaviorSnapshot
from thoth.domain.behavior_policy import PromptBehaviorPolicy
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evaluation_run import sealed_payload
from thoth.domain.research_execution import research_work
from thoth.ports.behavior_execution import BehaviorWorkControlPort


def behavior_context() -> BehaviorWorkContext:
    snapshot = ActiveBehaviorSnapshot.model_validate(
        sealed_payload(
            "ACTIVE_BEHAVIOR_SNAPSHOT",
            "snapshot_digest",
            {
                "project_id": "p",
                "component": "PROMPT_BUNDLE",
                "environment": "LOCAL",
                "artifact_ref": None,
                "content_digest": "a" * 64,
                "policy": PromptBehaviorPolicy(task_guidance="Keep the answer concise."),
                "origin": "BUILTIN_DEFAULT",
                "registry_revision": 0,
                "exposure_ref": None,
                "ignored_registry_digest": None,
                "reason_code": None,
            },
        )
    )
    return BehaviorWorkContext("p", (snapshot,))


@pytest.mark.asyncio
async def test_capture_is_after_language_usage_and_behavior_rendering() -> None:
    work = research_fixture("en")
    token = research_work.set(work)
    original = generator_request()
    delegate = RecordingGroundingModel()
    try:
        with behavior_work_scope(behavior_context()):
            result = await ResearchModel(BehaviorBoundModel(delegate)).structured(original)
        effective = delegate.calls[0]
        assert effective.response_language == "en"
        assert effective.context_pack.research_context["usage_observation"] == {"input_tokens": 11}
        assert (
            effective.context_pack.policy_hints["behavior_guidance"] == "Keep the answer concise."
        )
        captured = work.grounding_stages["GENERATOR"]
        assert captured.metadata.effective_context_digest == domain_digest(
            "GROUNDING_EFFECTIVE_CONTEXT", "1.0.0", canonical_payload(effective.context_pack)
        )
        assert captured.metadata.provider_input_digest == result.input_digest
        assert captured.metadata.provider_input_digest != captured.metadata.effective_context_digest
        assert captured.metadata.behavior_basis
        assert (
            original.context_pack.research_context == {}
            and original.context_pack.policy_hints == {}
        )
        assert captured.evidence == effective.context_pack.evidence
        with pytest.raises(
            GroundingPublicationHold, match="GROUNDING_PUBLICATION_BINDING_MISMATCH"
        ) as held:
            await ResearchModel(BehaviorBoundModel(delegate)).structured(original)
        assert held.value.cause_code == "GROUNDING_STAGE_ALREADY_CAPTURED"
        assert len(delegate.calls) == 1
        assert work.grounding_stages["GENERATOR"] is captured
    finally:
        research_work.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("fixture failure"), CancelledError()])
async def test_failed_or_cancelled_call_leaves_no_capture(error: BaseException) -> None:
    work = research_fixture()
    token = research_work.set(work)
    try:
        delegate = RecordingGroundingModel(error=error)
        with pytest.raises(type(error)):
            await ResearchModel(BehaviorBoundModel(delegate)).structured(generator_request())
        assert work.grounding_stages == {}
    finally:
        research_work.reset(token)


@pytest.mark.asyncio
async def test_outer_policy_failure_rolls_back_the_successful_provider_capture() -> None:
    class RejectAfterCall:
        def validate_work(self, snapshots: object) -> None:
            del snapshots
            raise RuntimeError("policy changed after the provider returned")

    work = research_fixture()
    token = research_work.set(work)
    behavior = behavior_context()
    behavior.control = cast(BehaviorWorkControlPort, RejectAfterCall())
    delegate = RecordingGroundingModel()
    try:
        with behavior_work_scope(behavior), pytest.raises(RuntimeError, match="policy changed"):
            await ResearchModel(BehaviorBoundModel(delegate)).structured(generator_request())
        assert len(delegate.calls) == 1 and work.grounding_stages == {}
    finally:
        research_work.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("project_id", "other", "GROUNDING_STAGE_PROJECT_MISMATCH"),
        ("text_sha256", "0" * 64, "GROUNDING_SUPPLIED_TEXT_HASH_MISMATCH"),
    ],
)
async def test_invalid_supplied_source_is_rejected_before_the_model(
    field: str,
    value: str,
    reason: str,
) -> None:
    work = research_fixture()
    token = research_work.set(work)
    request = generator_request()
    spans = list(request.context_pack.evidence)
    spans[0] = spans[0].model_copy(update={field: value})
    request = replace(
        request, context_pack=request.context_pack.model_copy(update={"evidence": tuple(spans)})
    )
    delegate = RecordingGroundingModel()
    try:
        with pytest.raises(
            GroundingPublicationHold, match="GROUNDING_PUBLICATION_BINDING_MISMATCH"
        ) as held:
            await ResearchModel(BehaviorBoundModel(delegate)).structured(request)
        assert held.value.cause_code == reason
        assert delegate.calls == [] and work.grounding_stages == {}
    finally:
        research_work.reset(token)


@pytest.mark.asyncio
async def test_generator_and_filler_capture_separate_inputs_on_acceptance_path() -> None:
    work = research_fixture("en")
    token = research_work.set(work)
    request = generator_request()
    delegate = RecordingGroundingModel()
    model = ResearchModel(BehaviorBoundModel(delegate))
    try:
        generated = await model.structured(request)

        # The normal accept entry chooses v5 by its receive type; no storage/server is invoked.
        class Command:
            cutoff_at = request.cutoff_at
            model_policy_ref = request.model_policy_ref
            evidence = request.context_pack.evidence

        accepted = await accepted_portfolio(
            model, generated.output, request.context_pack, Command(), False
        )
        assert len(delegate.calls) == 2
        assert set(work.grounding_stages) == {"GENERATOR", "FILLER"}
        assert (
            work.grounding_stages["GENERATOR"].metadata.effective_context_digest
            != work.grounding_stages["FILLER"].metadata.effective_context_digest
        )
        assert work.hypothesis_grounding is not None
        assert work.hypothesis_grounding.row_stages[("h1", "t-h1", "h3")] == "FILLER"
        assert accepted is work.hypothesis_grounding.portfolio
        assert '"rationale"' not in delegate.calls[1].context_pack.model_dump_json()
        assert [call.max_output_tokens for call in delegate.calls] == [8000, 3000]
        assert work.grounding_stages["GENERATOR"].metadata.max_output_tokens == 8000
        assert work.grounding_stages["FILLER"].metadata.max_output_tokens == 3000
    finally:
        research_work.reset(token)


@pytest.mark.asyncio
async def test_invalid_provider_digest_becomes_publication_hold_without_capture() -> None:
    class InvalidDigest(RecordingGroundingModel):
        async def structured(self, request):
            return replace(await super().structured(request), input_digest="invalid")

    work = research_fixture()
    token = research_work.set(work)
    try:
        with pytest.raises(
            GroundingPublicationHold, match="GROUNDING_PUBLICATION_BINDING_MISMATCH"
        ) as held:
            await ResearchModel(BehaviorBoundModel(InvalidDigest())).structured(generator_request())
        assert held.value.cause_code == "GROUNDING_STAGE_METADATA_INVALID"
        assert work.grounding_stages == {}
    finally:
        research_work.reset(token)
