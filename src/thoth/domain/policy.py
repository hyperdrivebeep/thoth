from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal, cast

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.enums import AuthorityState, CutoffState, SecurityClass
from thoth.domain.governance import ProjectPolicy
from thoth.domain.ids import ProjectId, Sha256


class PolicyDenialBasis(StrEnum):
    POLICY_NOT_FOUND = "POLICY_NOT_FOUND"
    POLICY_CONFIGURATION_INVALID = "POLICY_CONFIGURATION_INVALID"
    POLICY_BINDING_MISMATCH = "POLICY_BINDING_MISMATCH"
    POLICY_DIGEST_MISMATCH = "POLICY_DIGEST_MISMATCH"
    POLICY_EMPTY_CONNECTOR_ALLOWLIST = "POLICY_EMPTY_CONNECTOR_ALLOWLIST"
    SCOPE_DENIED = "SCOPE_DENIED"
    SECURITY_CLASS_DENIED = "SECURITY_CLASS_DENIED"
    EGRESS_DENIED = "EGRESS_DENIED"
    SANDBOX_RUNTIME_DENIED = "SANDBOX_RUNTIME_DENIED"
    SANDBOX_NETWORK_DENIED = "SANDBOX_NETWORK_DENIED"


class PolicyExpectation(DomainModel):
    policy_id: str = Field(min_length=1, max_length=160)
    policy_revision: int = Field(ge=1)
    policy_digest: Sha256


class AcquisitionRoute(DomainModel):
    evidence_group: str = Field(min_length=1, max_length=160)
    match_terms: tuple[str, ...] = Field(min_length=1)
    connector_id: str = Field(min_length=1, max_length=160)
    selector: dict[str, JsonValue]
    query_families: tuple[str, ...] = Field(min_length=1)
    max_waves: int = Field(default=1, ge=1, le=8)
    max_results: int = Field(default=1, ge=1, le=50)

    @model_validator(mode="after")
    def read_only_bounded_route(self) -> AcquisitionRoute:
        if not self.selector:
            raise ValueError("acquisition route requires a bounded selector")
        if len(self.match_terms) != len(set(term.lower() for term in self.match_terms)):
            raise ValueError("acquisition route match terms must be unique")
        return self


class CounterSearchRoute(DomainModel):
    route_id: str = Field(default="counter-route", min_length=1, max_length=160)
    target_loci: tuple[str, ...] = Field(min_length=1)
    connector_id: str = Field(min_length=1, max_length=160)
    selector: dict[str, JsonValue]
    query_families: tuple[str, ...] = Field(min_length=1)
    source_territory: str = Field(min_length=1, max_length=160)
    independence_group: str = Field(min_length=1, max_length=160)
    alternative_explanation: str = Field(min_length=1, max_length=2_000)
    source_authority: AuthorityState = AuthorityState.OFFICIAL
    temporal_state: CutoffState = CutoffState.ELIGIBLE
    support_match_terms: tuple[str, ...] = ()
    counter_match_terms: tuple[str, ...] = ()
    context_tags: tuple[str, ...] = ()
    max_waves: int = Field(default=1, ge=1, le=3)
    max_results: int = Field(default=3, ge=1, le=20)
    priority: int = Field(default=50, ge=0, le=100)
    depth: int = Field(default=1, ge=0, le=8)
    excursion: bool = False
    checkpoint_after: bool = False
    estimated_cost_microunits: int = Field(default=1, ge=0, le=1_000_000)

    @model_validator(mode="after")
    def bounded_typed_counter_route(self) -> CounterSearchRoute:
        if not self.selector:
            raise ValueError("counter-search route requires a bounded selector")
        if not self.support_match_terms and not self.counter_match_terms:
            raise ValueError("counter-search route requires support or counter match terms")
        return self


class CounterSearchLimits(DomainModel):
    max_waves: int = Field(default=1, ge=1, le=12)
    max_results: int = Field(default=20, ge=1, le=1_000)
    max_documents: int = Field(default=6, ge=1, le=100)
    max_bytes: int = Field(default=64 * 1024 * 1024, ge=1, le=512 * 1024 * 1024)
    max_model_calls: int = Field(default=0, ge=0, le=100)
    max_tool_calls: int = Field(default=8, ge=0, le=100)
    max_time_seconds: int = Field(default=60, ge=1, le=3_600)
    max_cost_microunits: int = Field(default=100, ge=0, le=10_000_000)
    max_depth: int = Field(default=2, ge=0, le=8)
    min_independent_confirmations: int = Field(default=1, ge=1, le=10)
    saturation_voi_threshold: Decimal = Field(default=Decimal("0.10"), ge=0, le=1)


class SandboxActionTemplate(DomainModel):
    action_family: str = Field(min_length=1, max_length=160)
    runtime_profile: str = Field(pattern=r"^(SCRIPTED|DOCKER_POC|GVISOR|FIRECRACKER|MANAGED)$")
    image_digest: str = Field(min_length=1, max_length=500)
    argv: tuple[str, ...] = Field(min_length=1)
    network_policy: str = Field(default="DENY_ALL", pattern=r"^(DENY_ALL|ALLOWLIST)$")
    allowed_hosts: tuple[str, ...] = ()
    resource_limits: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bounded_template(self) -> SandboxActionTemplate:
        if self.network_policy == "DENY_ALL" and self.allowed_hosts:
            raise ValueError("DENY_ALL sandbox template cannot include hosts")
        if self.network_policy == "ALLOWLIST" and not self.allowed_hosts:
            raise ValueError("ALLOWLIST sandbox template requires hosts")
        return self


class R2RecoveryPolicy(DomainModel):
    max_transient_retries: int = Field(default=0, ge=0, le=5)
    transient_markers: tuple[str, ...] = ("TRANSIENT_INFRA", "BOOT_FAILED", "TIMED_OUT")
    semantic_markers: tuple[str, ...] = ("SEMANTIC",)
    code_markers: tuple[str, ...] = ("CODE",)
    ambiguous_markers: tuple[str, ...] = ("AMBIGUOUS_EXTERNAL",)


class AuthoritativeExecutionPolicy(DomainModel):
    policy_id: str = Field(min_length=1, max_length=160)
    project_id: ProjectId
    policy_revision: int = Field(ge=1)
    policy_digest: Sha256
    connector_allowlist: tuple[str, ...]
    connector_allowed_egress_classes: tuple[str, ...]
    max_source_security_class: SecurityClass
    max_query_egress_security_class: SecurityClass = SecurityClass.PUBLIC
    sandbox_runtime_allowlist: tuple[str, ...]
    sandbox_network_policy: str = Field(pattern=r"^(DENY_ALL|ALLOWLIST)$")
    sandbox_allowed_hosts: tuple[str, ...]
    public_web_enabled: bool = False
    preferred_hosts: tuple[str, ...] = ()
    workspace_internet_grant_id: str | None = None
    acquisition_routes: tuple[AcquisitionRoute, ...] = ()
    counter_search_routes: tuple[CounterSearchRoute, ...] = ()
    counter_search_limits: CounterSearchLimits = CounterSearchLimits()
    sandbox_action_templates: tuple[SandboxActionTemplate, ...] = ()
    r2_recovery_policy: R2RecoveryPolicy = R2RecoveryPolicy()

    @model_validator(mode="after")
    def validate_network_policy(self) -> AuthoritativeExecutionPolicy:
        if self.sandbox_network_policy == "DENY_ALL" and self.sandbox_allowed_hosts:
            raise ValueError("DENY_ALL project policy cannot include sandbox hosts")
        if self.sandbox_network_policy == "ALLOWLIST" and not self.sandbox_allowed_hosts:
            raise ValueError("ALLOWLIST project policy requires sandbox hosts")
        return self

    @classmethod
    def from_project_policy(cls, value: ProjectPolicy) -> AuthoritativeExecutionPolicy:
        payload = value.payload
        return cls(
            policy_id=value.policy_id,
            project_id=value.project_id,
            policy_revision=value.version,
            policy_digest=value.policy_digest,
            connector_allowlist=_string_tuple(payload, "connector_allowlist"),
            connector_allowed_egress_classes=_string_tuple(
                payload, "connector_allowed_egress_classes"
            ),
            max_source_security_class=SecurityClass(
                _required_string(payload, "max_source_security_class")
            ),
            max_query_egress_security_class=SecurityClass(
                str(payload.get("max_query_egress_security_class", "PUBLIC"))
            ),
            sandbox_runtime_allowlist=_string_tuple(payload, "sandbox_runtime_allowlist"),
            sandbox_network_policy=_required_string(payload, "sandbox_network_policy"),
            sandbox_allowed_hosts=_string_tuple(payload, "sandbox_allowed_hosts"),
            public_web_enabled=public_web_enabled_from_payload(payload),
            preferred_hosts=preferred_hosts_from_payload(payload),
            workspace_internet_grant_id=workspace_internet_grant_id_from_payload(payload),
            acquisition_routes=tuple(
                AcquisitionRoute.model_validate(item)
                for item in _object_array(payload, "acquisition_routes")
            ),
            counter_search_routes=tuple(
                CounterSearchRoute.model_validate(item)
                for item in _object_array(payload, "counter_search_routes")
            ),
            counter_search_limits=CounterSearchLimits.model_validate(
                payload.get("counter_search_limits", {})
            ),
            sandbox_action_templates=tuple(
                SandboxActionTemplate.model_validate(item)
                for item in _object_array(payload, "sandbox_action_templates")
            ),
            r2_recovery_policy=R2RecoveryPolicy.model_validate(
                payload.get("r2_recovery_policy", {})
            ),
        )

    @property
    def expectation(self) -> PolicyExpectation:
        return PolicyExpectation(
            policy_id=self.policy_id,
            policy_revision=self.policy_revision,
            policy_digest=self.policy_digest,
        )


class PolicyDenialReceipt(DomainModel):
    project_id: ProjectId
    action_kind: Literal["CONNECTOR", "SANDBOX"]
    denial_basis: PolicyDenialBasis
    policy_id: str | None = Field(default=None, max_length=160)
    policy_revision: int | None = Field(default=None, ge=1)
    policy_digest: Sha256 | None = None
    requested_policy_id: str | None = Field(default=None, max_length=160)
    requested_policy_revision: int | None = Field(default=None, ge=1)
    requested_policy_digest: Sha256 | None = None
    denied_at: AwareDatetime
    receipt_digest: Sha256
    semantic_truth_certified: Literal[False] = False


def public_web_enabled_from_payload(payload: dict[str, object]) -> bool:
    block = payload.get("public_web")
    if not isinstance(block, dict):
        return False
    values = cast(dict[object, object], block)
    return bool(values.get("enabled"))


def preferred_hosts_from_payload(payload: dict[str, object]) -> tuple[str, ...]:
    block = payload.get("public_web")
    if not isinstance(block, dict):
        return ()
    values = cast(dict[object, object], block)
    value = values.get("preferred_hosts")
    if not isinstance(value, (list, tuple)):
        return ()
    items = cast(list[object] | tuple[object, ...], value)
    return tuple(str(item) for item in items if isinstance(item, str) and item)


def workspace_internet_grant_id_from_payload(payload: dict[str, object]) -> str | None:
    block = payload.get("public_web")
    if not isinstance(block, dict):
        return None
    values = cast(dict[object, object], block)
    value = values.get("workspace_grant_id")
    return value if isinstance(value, str) and value else None


def _required_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"project policy requires non-empty {key}")
    return value


def _string_tuple(payload: dict[str, object], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"project policy requires string array {key}")
    items = cast(list[object] | tuple[object, ...], value)
    if any(not isinstance(item, str) or not item for item in items):
        raise ValueError(f"project policy requires string array {key}")
    values = tuple(cast(str, item) for item in items)
    if len(values) != len(set(values)):
        raise ValueError(f"project policy {key} must not contain duplicates")
    return values


def _object_array(payload: dict[str, object], key: str) -> tuple[dict[str, object], ...]:
    value = payload.get(key, ())
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"project policy requires object array {key}")
    items = cast(list[object] | tuple[object, ...], value)
    if any(not isinstance(item, dict) for item in items):
        raise ValueError(f"project policy requires object array {key}")
    return tuple(
        {str(child_key): child for child_key, child in cast(dict[object, object], item).items()}
        for item in items
    )
