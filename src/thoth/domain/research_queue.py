"""Durable v2 input admission before a request revision exists."""

from typing import Literal

from pydantic import Field, JsonValue, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.ids import Sha256
from thoth.domain.model_settings import ResolvedModelSettings
from thoth.domain.research_reference import RevisionRef


class QueuedResearchInput(DomainModel):
    record_kind: Literal["QueuedResearchInput"] = "QueuedResearchInput"
    schema_version: Literal["1.0.0"] = "1.0.0"
    project_id: str
    thread_id: str
    operation_id: str
    idempotency_key: str
    input_id: str
    ordinal: int = Field(ge=1)
    after_operation_id: str
    accepted_head_digest: Sha256
    instruction: str = Field(min_length=1, max_length=20_000)
    edit_kind: Literal["APPEND", "REPLACE"] = "APPEND"
    continuation: dict[str, JsonValue] = Field(default_factory=dict)
    actor_ref: str
    owner_session_id: str | None = None
    owner_role_assignment_id: str | None = None
    owner_data_scopes: tuple[str, ...] = ()
    scope: dict[str, str]
    cutoff_at: str
    policy_ref: str
    policy_digest: str
    source_binding_ids: tuple[str, ...] = ()
    model_settings: ResolvedModelSettings
    state: Literal["QUEUED", "ACTIVE", "HOLD", "SUPERSEDED"] = "QUEUED"
    hold_reason: str | None = None
    activated_request_ref: RevisionRef | None = None

    @model_validator(mode="after")
    def validate_state(self) -> "QueuedResearchInput":
        if self.state == "ACTIVE" and self.activated_request_ref is None:
            raise ValueError("QUEUE_ACTIVE_REQUEST_REQUIRED")
        if self.state in {"HOLD", "SUPERSEDED"} and self.hold_reason is None:
            raise ValueError("QUEUE_HOLD_REASON_REQUIRED")
        return self
