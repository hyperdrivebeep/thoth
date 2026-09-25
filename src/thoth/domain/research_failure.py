"""Execution diagnostics, separate from scientific result revisions."""

import re
from typing import Literal

from pydantic import AwareDatetime, Field, ValidationError

from thoth.domain.base import DomainModel
from thoth.domain.research_request import RevisionRef

FailureOrigin = Literal[
    "MODEL_CALL",
    "RESEARCH_EXECUTION",
    "RESULT_PUBLICATION",
    "HOLD_PUBLICATION",
    "FAILURE_RECORDING",
    "LEASE_RELEASE",
]


class ResearchFailureCause(DomainModel):
    reason_code: str
    exception_type: str
    origin: FailureOrigin
    detail: str | None = Field(default=None, max_length=500)


def _validation_detail(error: BaseException) -> str | None:
    if not isinstance(error, ValidationError):
        return None
    items: list[str] = []
    try:
        for item in error.errors(include_url=False, include_input=False)[:8]:
            location = ".".join(str(part) for part in item.get("loc", ())) or "root"
            items.append(f"{location}:{item.get('type')}:{item.get('msg')}")
    except Exception:
        return None
    text = " | ".join(items).strip()
    return text[:500] or None


def failure_cause(error: BaseException, origin: FailureOrigin) -> ResearchFailureCause:
    raw = str(error).replace("\n", " ").strip()
    code = raw if re.fullmatch(r"[A-Z][A-Z0-9_]{0,159}", raw) else "RESEARCH_INTERNAL_ERROR"
    return ResearchFailureCause(
        reason_code=code,
        exception_type=type(error).__name__,
        origin=origin,
        detail=_validation_detail(error),
    )


class ResearchFailureRecord(DomainModel):
    record_kind: Literal["ResearchFailureRecord"] = "ResearchFailureRecord"
    schema_version: Literal["1.0.0"] = "1.0.0"
    project_id: str
    thread_id: str
    operation_id: str
    request_ref: RevisionRef
    attempt_epoch: int
    phase: str
    primary: ResearchFailureCause
    secondary: ResearchFailureCause | None = None
    last_checkpoint_ref: RevisionRef | None = None
    remote_observation: str = "UNKNOWN"
    retry_requires_user_action: bool = True
    created_at: AwareDatetime


class ResearchPublicationError(RuntimeError):
    def __init__(self, original: Exception) -> None:
        self.original = original
        super().__init__("RESEARCH_RESULT_PUBLICATION_FAILED")


class ResearchCleanupFailure(DomainModel):
    record_kind: Literal["ResearchCleanupFailure"] = "ResearchCleanupFailure"
    schema_version: Literal["1.0.0"] = "1.0.0"
    operation_id: str
    attempt_epoch: int
    cause: ResearchFailureCause
    created_at: AwareDatetime
