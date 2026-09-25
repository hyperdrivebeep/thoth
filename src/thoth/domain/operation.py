from __future__ import annotations

from pydantic import AwareDatetime, JsonValue

from thoth.domain.base import DomainModel
from thoth.domain.enums import OperationState
from thoth.domain.ids import OperationId, ProjectId, Sha256
from thoth.domain.resource_scope import ResourceUse


class InternalFailureStateSnapshot(DomainModel):
    working_head_count: int
    semantic_revision_count: int
    memory_record_count: int
    memory_revision_count: int
    canonical_receipt_count: int
    memory_receipt_count: int


class InternalFailureTopFrame(DomainModel):
    file: str
    function: str
    line: int


class InternalFailureDiagnostic(DomainModel):
    exception_type: str
    top_frame: InternalFailureTopFrame
    fingerprint: Sha256
    operation_id: OperationId
    input_consumed: bool
    pre_state: InternalFailureStateSnapshot
    post_state: InternalFailureStateSnapshot


class OperationRecord(DomainModel):
    operation_id: OperationId
    project_id: ProjectId
    method: str
    idempotency_key: str
    scope_digest: Sha256
    owner_actor_id: str | None = None
    owner_session_id: str | None = None
    owner_role_assignment_id: str | None = None
    owner_data_scopes: tuple[str, ...] = ()
    state: OperationState
    result: dict[str, JsonValue] | None = None
    error: dict[str, JsonValue] | None = None
    created_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    epoch: int = 0
    schema_version: str = "1.0.0"
    resource_uses: tuple[ResourceUse, ...] | None = None
