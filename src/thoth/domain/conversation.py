from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, Field, JsonValue

from thoth.domain.base import DomainModel
from thoth.domain.ids import Sha256


class ConversationIntent(StrEnum):
    SELECT_PROJECT = "SELECT_PROJECT"
    SELECT_THREAD = "SELECT_THREAD"
    SELECT_MODEL = "SELECT_MODEL"
    SET_REASONING = "SET_REASONING"
    RESET_MODEL = "RESET_MODEL"
    NEW_THREAD_CONTEXT = "NEW_THREAD_CONTEXT"
    START_THREAD = "START_THREAD"
    CONTINUE_THREAD = "CONTINUE_THREAD"
    ADD_SOURCE = "ADD_SOURCE"
    ASK_STATUS = "ASK_STATUS"
    COMPARE_REVISION = "COMPARE_REVISION"
    RESTORE_PREVIEW = "RESTORE_PREVIEW"
    PAUSE_THREAD = "PAUSE_THREAD"
    RESUME_THREAD = "RESUME_THREAD"
    STOP_THREAD = "STOP_THREAD"
    EXPORT_LOCAL = "EXPORT_LOCAL"
    UNKNOWN_HOLD = "UNKNOWN_HOLD"
    RETRY_THREAD = "RETRY_THREAD"
    REFRESH_USAGE = "REFRESH_USAGE"


class ConversationIntentState(StrEnum):
    READY = "READY"
    HOLD = "HOLD"


class TuiTurnStatus(StrEnum):
    DISPATCHED = "DISPATCHED"
    HOLD = "HOLD"


class TuiSessionState(DomainModel):
    session_id: str
    active_project_id: str | None = None
    active_thread_id: str | None = None
    revision: int = Field(ge=0)
    updated_at: AwareDatetime
    raw_free_text_stored: Literal[False] = False


class ConversationIntentCandidate(DomainModel):
    intent: ConversationIntent
    state: ConversationIntentState
    method: str | None = None
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    missing_fields: tuple[str, ...] = ()
    display_message: str
    raw_input_digest: Sha256
    raw_text_retained: Literal[False] = False
    canonical_state_changed: Literal[False] = False
    authority_required: bool = False


class ConversationDispatchOutcome(DomainModel):
    success: bool
    value: dict[str, JsonValue] = Field(default_factory=dict)
    error_code: int | None = None
    error_message: str | None = None


class TuiTurnResult(DomainModel):
    status: TuiTurnStatus
    candidate: ConversationIntentCandidate
    session: TuiSessionState
    response: dict[str, JsonValue] = Field(default_factory=dict)
    error_code: int | None = None
    error_message: str | None = None
