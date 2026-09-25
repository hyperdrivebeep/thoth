"""Per-attempt context shared by model/connector dispatch boundaries."""

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from thoth.domain.cleanup import CleanupUsage
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.model import ContextPack
from thoth.domain.model_dispatch import (
    ModelCallContext,
    ModelControlCapability,
    ModelReceiveObservation,
)
from thoth.domain.model_settings import ResolvedModelSettings
from thoth.domain.oauth_retry import OAuthRetryPolicy
from thoth.domain.research_request import RevisionRef
from thoth.domain.revision import StagedRevision


class ResearchFence(RuntimeError):
    pass


class ResearchBoundary(Protocol):
    def check(self) -> None: ...
    def reserve(self, payload_bytes: int, output_tokens: int = 0) -> None: ...
    def transport(self, payload_bytes: int) -> None: ...
    def call_timeout(self) -> float | None: ...
    def owns_attempt(self) -> bool: ...
    def new_model_call(self) -> str: ...
    def reserve_dispatch(
        self,
        dispatch_id: str,
        payload: bytes,
        output_tokens: int,
        capability: ModelControlCapability,
    ) -> None: ...
    def record_usage(
        self,
        dispatch_id: str,
        received_bytes: int,
        input_tokens: int | None,
        output_tokens: int | None,
        remote_stop: str,
        response_id: str | None,
        observation: ModelReceiveObservation | None = None,
        retry_of_dispatch_id: str | None = None,
        cached_input_tokens: int | None = None,
    ) -> None: ...


@runtime_checkable
class ResearchUsageObserver(Protocol):
    def usage_observation(self) -> dict[str, object]: ...


@dataclass
class ResearchWork:
    request_ref: RevisionRef
    effective_question: str
    boundary: ResearchBoundary
    evidence: tuple[EvidenceSpan, ...] = ()
    context: dict[str, object] = field(default_factory=dict)
    record_refs: list[RevisionRef] = field(default_factory=list)
    pending_revisions: list[StagedRevision] = field(default_factory=list)
    prepare: Callable[[], Awaitable[None]] | None = None
    show_draft: Callable[[str, dict[str, object]], None] | None = None
    model_settings: ResolvedModelSettings | None = None
    observe_external_effect: Callable[[str, str], None] | None = None
    cleanup_usage: dict[str, CleanupUsage] = field(default_factory=dict)
    preprocessing_cache: dict[str, object] = field(default_factory=dict)
    oauth_retry_policy: OAuthRetryPolicy | None = None
    consumed_heads: dict[str, str] = field(default_factory=dict)
    produced_refs: list[RevisionRef] = field(default_factory=list)
    produced_final_heads: dict[str, str] = field(default_factory=dict)
    produced_receipt_refs: list[str] = field(default_factory=list)
    memory_revision_refs: list[str] = field(default_factory=list)
    basis_reasons: list[str] = field(default_factory=list)
    consumed_sources: dict[str, EvidenceSpan] = field(default_factory=dict)
    observe_context: Callable[[ContextPack], None] | None = None


research_work: ContextVar[ResearchWork | None] = ContextVar("research_work", default=None)
model_call: ContextVar[ModelCallContext | None] = ContextVar("model_call", default=None)


def reserve_model_dispatch(
    payload: bytes, output_tokens: int, capability: ModelControlCapability
) -> str:
    work, call = research_work.get(), model_call.get()
    if work is None:
        return "unscoped"
    if call is None:
        call = ModelCallContext(work.boundary.new_model_call())
        model_call.set(call)
    dispatch_id = f"{call.call_id}:{call.dispatch_index}"
    call.dispatch_index += 1
    work.boundary.reserve_dispatch(dispatch_id, payload, output_tokens, capability)
    return dispatch_id


def check_research_boundary() -> None:
    work = research_work.get()
    if work is not None:
        work.boundary.check()
