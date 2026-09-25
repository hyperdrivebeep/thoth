from __future__ import annotations

from pydantic import AwareDatetime, Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.ids import CycleId, ProjectId, Sha256, ThreadId


class InvestigationRecord(DomainModel):
    investigation_id: str
    project_id: ProjectId
    thread_id: ThreadId
    cycle_id: CycleId
    parent_investigation_id: str | None = None
    trigger: str
    question: str
    target_object_id: str | None = None
    target_hypothesis_id: str | None = None
    mode: str = "BOUNDED"
    scope: dict[str, str] = Field(default_factory=dict)
    required_evidence_groups: tuple[str, ...] = ()
    query_families: tuple[str, ...] = ()
    counter_search_policy: str = "REQUIRED_BEFORE_CONCLUSION"
    budget: int = Field(default=30, ge=1)
    budget_usage: int = Field(default=0, ge=0)
    stop_conditions: tuple[str, ...] = ()
    domain_state: str = "ACTIVE"
    execution_state: str = "IDLE"
    current_wave: int = Field(default=0, ge=0)
    observation_count: int = Field(default=0, ge=0)
    open_lead_count: int = Field(default=0, ge=0)
    claim_candidate_count: int = Field(default=0, ge=0)
    gap_count: int = Field(default=0, ge=0)
    sufficiency: dict[str, object] = Field(default_factory=dict)
    checkpoint_digest: Sha256 | None = None
    result: dict[str, object] | None = None
    plan_revision: int = Field(default=0, ge=0)
    investigation_digest: Sha256
    created_at: AwareDatetime
    updated_at: AwareDatetime
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def enforce_investigation_contract(self) -> InvestigationRecord:
        if self.mode not in {"BOUNDED", "CRITICAL", "SATURATION"}:
            raise ValueError("unsupported investigation mode")
        if self.mode == "SATURATION" and "EXPLICIT_SATURATION_OPT_IN" not in self.stop_conditions:
            raise ValueError("SATURATION requires explicit opt-in marker")
        if self.budget_usage > self.budget:
            raise ValueError("investigation budget usage cannot exceed budget")
        if (
            self.domain_state in {"CONVERGED", "HOLD", "ABSTAINED", "STOPPED"}
            and self.result is None
        ):
            raise ValueError("terminal investigation requires result")
        return self


class InvestigationAuditRecord(DomainModel):
    audit_id: str
    project_id: ProjectId
    investigation_id: str
    event_type: str
    payload: dict[str, object]
    event_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"
