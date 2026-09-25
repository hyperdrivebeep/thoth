from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from thoth.domain.enums import AuthorityState, CutoffState, RiskTier, SecurityClass
from thoth.domain.resource_scope import ResourceScopePolicy


class PackProject(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pack_id: str
    project_id: str
    name: str
    cutoff_at: AwareDatetime
    overlay: str
    policy_binding_ref: str


class PackScenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    thread_id: str
    cycle_id: str
    object_id: str
    problem: str


class PackSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    path: str
    media_type: str
    authority: AuthorityState
    cutoff_state: CutoffState
    security_class: SecurityClass
    version_label: str | None = None
    byte_sha256: str = Field(pattern=r"^[A-Fa-f0-9]{64}$")
    rights: str


class PackPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str
    model_policy_ref: str
    external_write: bool = False
    allow_scripted_model: bool = False
    minimum_action_tier_by_family: dict[str, RiskTier]
    approver_role_by_family: dict[str, str] = Field(default_factory=dict)
    unknown_action_family_tier: RiskTier = RiskTier.R3
    sufficiency_signals: dict[str, bool | None] = Field(default_factory=dict)
    sealed_evidence_supported: bool = False
    resource_scope_policy: ResourceScopePolicy | None = None


class LoadedProjectPack(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    root: str
    project: PackProject
    scenario: PackScenario
    policy: PackPolicy
    sources: tuple[PackSource, ...]
    criteria_templates: tuple[dict[str, object], ...] = ()
    scripted_templates: dict[str, object] | None = None
