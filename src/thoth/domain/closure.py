from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, model_validator

from thoth.domain.actor import ActorRef
from thoth.domain.base import DomainModel
from thoth.domain.enums import ClosureStatus, ExportReleaseState
from thoth.domain.ids import ProjectId, Sha256, ThreadId


class ResidualRisk(DomainModel):
    description: str
    state: Literal["KNOWN", "UNKNOWN"]
    basis_refs: tuple[str, ...]


class ClosureOpenItem(DomainModel):
    """Legacy missing fields stay missing, so reads cannot manufacture readiness."""

    item_id: str | None = None
    description: str = ""
    disposition: str | None = None
    owner_ref: str | None = None
    followup_ref: str | None = None
    trigger_or_due: str | None = None
    residual_risk: ResidualRisk | None = None


class ClosureOpenItemV2(DomainModel):
    item_id: str
    description: str = ""
    disposition: str
    owner_ref: str | None = None
    followup_ref: str | None = None
    trigger_or_due: str
    residual_risk: ResidualRisk


class Closure(DomainModel):
    closure_id: str
    project_id: ProjectId
    thread_id: ThreadId | None = None
    status: ClosureStatus
    resolution: str
    unresolved_refs: tuple[str, ...] = ()
    open_effect_refs: tuple[str, ...] = ()
    retention_policy_ref: str
    actor: ActorRef
    closed_at: AwareDatetime | None = None
    head_set_digest: Sha256
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def closed_has_no_open_work(self) -> Closure:
        if self.status == ClosureStatus.CLOSED and (
            self.unresolved_refs or self.open_effect_refs or self.closed_at is None
        ):
            raise ValueError("closed state requires no unresolved or open effects and a timestamp")
        return self


class Export(DomainModel):
    export_id: str
    project_id: ProjectId
    purpose: str
    audience: str
    head_set_digest: Sha256
    artifact_refs: tuple[str, ...]
    receipt_refs: tuple[str, ...]
    release_state: ExportReleaseState = ExportReleaseState.LOCAL_DRAFT
    prepared_by: ActorRef
    external_release_receipt_ref: str | None = None
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def external_release_requires_receipt(self) -> Export:
        if (
            self.release_state == ExportReleaseState.EXTERNALLY_RELEASED
            and self.external_release_receipt_ref is None
        ):
            raise ValueError("external release requires a protected release receipt")
        return self
