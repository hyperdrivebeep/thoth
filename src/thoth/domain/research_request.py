"""Versioned request records. Current means a matching basis, never scientific truth."""

from typing import Literal, cast

from pydantic import Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.model_settings import ResolvedModelSettings
from thoth.domain.research_basis import ResearchResultBasis
from thoth.domain.research_reference import RevisionRef as RevisionRef
from thoth.domain.response_language import (
    LegacyResponseLanguage,
    ResponseLanguage,
    request_codec_version,
)


class ThreadRequestRevision(DomainModel):
    record_kind: Literal["ThreadRequestRevision"] = "ThreadRequestRevision"
    schema_version: Literal["2.0.0"] = "2.0.0"
    project_id: str
    thread_id: str
    request_epoch: int = Field(ge=1)
    parent_ref: RevisionRef | None = None
    accepted_input_ids: tuple[str, ...]
    edit_kind: Literal["APPEND", "REPLACE", "STEER"]
    authored_text: str
    authored_text_ref: RevisionRef
    effective_question: str
    scope: dict[str, str]
    cutoff_at: str
    policy_ref: str
    policy_digest: str
    actor_ref: str
    operation_id: str
    model_settings: ResolvedModelSettings | None = None


class ThreadRequestRevisionV21(ThreadRequestRevision):
    """Explicit response language belongs to one immutable request revision."""

    schema_version: Literal["2.1.0"] = "2.1.0"  # type: ignore[reportIncompatibleVariableOverride]
    response_language: LegacyResponseLanguage


class ThreadRequestRevisionV22(ThreadRequestRevision):
    """Same as 2.1.0 with the full language list; the codec that holds languages added later."""

    schema_version: Literal["2.2.0"] = "2.2.0"  # type: ignore[reportIncompatibleVariableOverride]
    response_language: ResponseLanguage


LANGUAGE_REQUEST_CODECS: dict[str, type[ThreadRequestRevisionV21 | ThreadRequestRevisionV22]] = {
    "2.1.0": ThreadRequestRevisionV21,
    "2.2.0": ThreadRequestRevisionV22,
}
LanguageRequest = ThreadRequestRevisionV21 | ThreadRequestRevisionV22
LANGUAGE_REQUEST_TYPES = (ThreadRequestRevisionV21, ThreadRequestRevisionV22)


def request_with_language(
    request: ThreadRequestRevision, language: ResponseLanguage
) -> LanguageRequest:
    """The request fixed to this language, in the oldest codec that can hold it."""
    version = request_codec_version(language)
    return LANGUAGE_REQUEST_CODECS[version].model_validate(
        {**request.model_dump(), "schema_version": version, "response_language": language}
    )


def decode_thread_request(content: object) -> ThreadRequestRevision:
    if isinstance(content, dict):
        codec = LANGUAGE_REQUEST_CODECS.get(
            str(cast(dict[str, object], content).get("schema_version"))
        )
        if codec is not None:
            return codec.model_validate(content)
    return ThreadRequestRevision.model_validate(content)


class AuthoredInputRevision(DomainModel):
    record_kind: Literal["AuthoredInputRevision"] = "AuthoredInputRevision"
    schema_version: Literal["2.0.0"] = "2.0.0"
    project_id: str
    thread_id: str
    input_id: str
    text: str
    actor_ref: str


class ResolvedRequestRevision(DomainModel):
    record_kind: Literal["ResolvedRequestRevision"] = "ResolvedRequestRevision"
    schema_version: Literal["2.0.0"] = "2.0.0"
    request_ref: RevisionRef
    object_id: str
    effective_question: str
    profile_ref: str
    preference_refs: tuple[str, ...] = ()
    unresolved_interpretations: tuple[str, ...] = ()
    interpretation_run: str
    connected_sources_only: bool = False


class CurrentResultPayload(DomainModel):
    record_kind: Literal["CurrentResultManifest"] = "CurrentResultManifest"
    request_ref: RevisionRef
    operation_id: str
    attempt_epoch: int
    basis_digest: str
    phase: str
    state: Literal["PARTIAL", "ASSESSED_FOR_REQUEST"] = "PARTIAL"
    completion: Literal["CHECKPOINT", "TERMINAL"] = "CHECKPOINT"
    input_ids: tuple[str, ...]
    record_refs: tuple[RevisionRef, ...] = ()
    source_refs: tuple[str, ...] = ()
    source_basis: dict[str, str] = Field(default_factory=dict)
    source_context_digest: str | None = None
    source_context_version: str | None = None
    resource_uses: tuple[dict[str, object], ...] = ()
    result: dict[str, object] = Field(default_factory=dict)
    gaps: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    terminal_reason: str | None = None


class CurrentResultManifest(CurrentResultPayload):
    schema_version: Literal["2.0.0"] = "2.0.0"


class CurrentResultManifestV21(CurrentResultPayload):
    schema_version: Literal["2.1.0"] = "2.1.0"
    research_basis: ResearchResultBasis

    @model_validator(mode="after")
    def require_exact_request_basis(self) -> "CurrentResultManifestV21":
        if self.research_basis.request_ref != self.request_ref:
            raise ValueError("RESULT_REQUEST_BASIS_MISMATCH")
        return self


class ResearchAttempt(DomainModel):
    record_kind: Literal["ResearchAttempt"] = "ResearchAttempt"
    schema_version: Literal["2.0.0"] = "2.0.0"
    operation_id: str
    request_ref: RevisionRef
    attempt_epoch: int = 1
    owner_actor_id: str | None
    owner_session_id: str | None
    captured_heads: dict[str, str]
    budget_ref: str
    phase: str = "ACCEPTED"
    status: str = "RUNNING"
    continuation: dict[str, object]
    checkpoint_ref: RevisionRef | None = None
    completed_stage_refs: tuple[RevisionRef, ...] = ()
    draft_progress: dict[str, object] = Field(default_factory=dict)
    event_cursor: str | None = None
    worker_id: str | None = None
    execution_observation: str = "NOT_STARTED"
    remote_observation: str = "UNKNOWN"
    external_effect_state: str = "NONE"
    external_effect_ref: str | None = None


class InputDelivery(DomainModel):
    record_kind: Literal["InputDelivery"] = "InputDelivery"
    schema_version: Literal["2.0.0"] = "2.0.0"
    input_id: str
    ordinal: int
    request_ref: RevisionRef
    state: Literal["ACCEPTED", "APPLIED", "SUPERSEDED", "CANCELLED"] = "ACCEPTED"
    first_result_ref: RevisionRef | None = None


class ResearchBudget(DomainModel):
    record_kind: Literal["ResearchBudget"] = "ResearchBudget"
    schema_version: Literal["2.0.0"] = "2.0.0"
    # Historical limits remain decodable but are not enforced by the current usage-only policy.
    max_calls: int | None = None
    # Legacy name/codec retained: operational non-model I/O bound, not model context capacity.
    max_prompt_bytes: int = 180_000
    # Historical values remain readable; cumulative reservation is no longer a cutoff.
    max_reserved_tokens: int | None = None
    max_seconds: int | None = None
    started_at: str
    calls: int = 0
    reserved_tokens: int = 0
    actual_tokens: int | None = None
    actual_cost: str | None = None
    usage_state: Literal["UNKNOWN"] = "UNKNOWN"


REQUEST_CODECS = {
    model.model_fields["record_kind"].default: model
    for model in (
        ThreadRequestRevision,
        ResolvedRequestRevision,
        CurrentResultManifest,
        ResearchAttempt,
        InputDelivery,
        ResearchBudget,
        AuthoredInputRevision,
    )
}
