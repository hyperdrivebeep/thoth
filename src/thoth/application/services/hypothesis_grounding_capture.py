"""Capture v5 call provenance after language/behavior rendering, without changing dispatch order."""

import hashlib
import json
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from thoth.application.services.behavior_context import current_behavior_work
from thoth.application.services.hypothesis_grounding_hold import (
    GroundingPublicationHold,
    is_capture_failure,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ModelRole
from thoth.domain.hypothesis_v5 import CONTRACT_V5, FILLER_V6
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.prediction_grounding import (
    GroundingCallCapture,
    GroundingStage,
    GroundingStageInput,
)
from thoth.domain.research_execution import ResearchWork, research_work
from thoth.ports.model import ModelPort


@dataclass
class _CaptureAttempt:
    work: ResearchWork
    stage: GroundingStage
    captured: GroundingCallCapture | None = None


_ATTEMPT: ContextVar[_CaptureAttempt | None] = ContextVar("grounding_capture_attempt", default=None)


def _stage[T: BaseModel](request: ModelRequest[T]) -> GroundingStage | None:
    if request.role == ModelRole.HYPOTHESIS_GENERATOR and request.prompt_version == CONTRACT_V5:
        return "GENERATOR"
    if request.role == ModelRole.HYPOTHESIS_TABLE_FILLER and request.prompt_version == FILLER_V6:
        return "FILLER"
    return None


@contextmanager
def grounding_capture_scope[T: BaseModel](request: ModelRequest[T]) -> Generator[None]:
    """Rollback only this call's capture if an outer policy/fence rejects its result."""
    work, stage = research_work.get(), _stage(request)
    if work is None or stage is None:
        yield
        return
    attempt = _CaptureAttempt(work, stage)
    token = _ATTEMPT.set(attempt)
    try:
        yield
    except BaseException as error:
        if attempt.captured is not None and work.grounding_stages.get(stage) is attempt.captured:
            del work.grounding_stages[stage]
        if is_capture_failure(error):
            raise GroundingPublicationHold(str(error)) from error
        raise
    finally:
        _ATTEMPT.reset(token)


async def dispatch_with_grounding[T: BaseModel](
    delegate: ModelPort, request: ModelRequest[T]
) -> ModelResult[T]:
    work = research_work.get()
    stage = _stage(request)
    if work is None or stage is None:
        return await delegate.structured(request)
    attempt = _ATTEMPT.get()
    if attempt is None or attempt.work is not work or attempt.stage != stage:
        raise ValueError("GROUNDING_CAPTURE_SCOPE_MISSING")
    if request.project_id != work.request_ref.project_id:
        raise ValueError("GROUNDING_STAGE_PROJECT_MISMATCH")
    context = request.context_pack.model_copy(deep=True)
    if context.project_id != request.project_id:
        raise ValueError("GROUNDING_STAGE_PROJECT_MISMATCH")
    if any(span.project_id != request.project_id for span in context.evidence):
        raise ValueError("GROUNDING_STAGE_PROJECT_MISMATCH")
    if len({span.span_id for span in context.evidence}) != len(context.evidence):
        raise ValueError("GROUNDING_SUPPLIED_SPAN_AMBIGUOUS")
    if any(
        hashlib.sha256(span.exact_text.encode()).hexdigest() != span.text_sha256.lower()
        for span in context.evidence
    ):
        raise ValueError("GROUNDING_SUPPLIED_TEXT_HASH_MISMATCH")
    if request.response_language != work.response_language:
        raise ValueError("GROUNDING_STAGE_LANGUAGE_MISMATCH")
    if stage in work.grounding_stages:
        raise ValueError("GROUNDING_STAGE_ALREADY_CAPTURED")
    behavior = current_behavior_work()
    language_policy = context.research_context.get("language_policy_version")
    metadata = {
        "generating_request_ref": work.request_ref,
        "prompt_version": request.prompt_version,
        "output_schema_digest": hashlib.sha256(
            json.dumps(
                request.output_model.model_json_schema(), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest(),
        "effective_context_digest": domain_digest(
            "GROUNDING_EFFECTIVE_CONTEXT", "1.0.0", canonical_payload(context)
        ),
        "response_language": request.response_language,
        "language_policy_version": language_policy
        if request.response_language is not None
        else None,
        "cutoff_at": request.cutoff_at,
        "model_policy_ref": request.model_policy_ref,
        "model_settings_digest": None
        if request.model_settings is None
        else request.model_settings.settings_digest,
        "behavior_basis": ()
        if behavior is None
        else tuple(item.snapshot_digest for item in behavior.snapshots),
        "max_output_tokens": request.max_output_tokens,
    }
    result = await delegate.structured(request)
    if result.prompt_version != request.prompt_version:
        raise ValueError("GROUNDING_PROVIDER_CONTRACT_MISMATCH")
    # Commit only a successful call. Exceptions/cancellation leave no fabricated capture.
    try:
        source = GroundingStageInput.model_validate(
            {**metadata, "provider_input_digest": result.input_digest}
        )
    except ValidationError as error:
        raise ValueError("GROUNDING_STAGE_METADATA_INVALID") from error
    if stage in work.grounding_stages:
        raise ValueError("GROUNDING_STAGE_ALREADY_CAPTURED")
    captured = GroundingCallCapture(metadata=source, evidence=context.evidence)
    work.grounding_stages[stage] = captured
    attempt.captured = captured
    return result
