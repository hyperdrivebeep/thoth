"""Resource ownership is independent of requester authority and source content."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.ids import Sha256

OwnerKind = Literal["PROJECT", "WORKSTREAM"]
Visibility = Literal["PROJECT_SHARED", "WORKSTREAM", "EXPLICIT_GRANT"]
GranteeKind = Literal["ACTOR", "WORKSTREAM"]


class ResourceScopeError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ResourceScopeTemplate(DomainModel):
    owner_kind: OwnerKind
    owner_workstream: str | None = Field(default=None, min_length=1, max_length=160)
    visibility: Visibility
    parent_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def explicit_owner(self) -> ResourceScopeTemplate:
        if (self.owner_kind == "WORKSTREAM") != (self.owner_workstream is not None):
            raise ValueError("RESOURCE_OWNER_WORKSTREAM_MISMATCH")
        if self.visibility == "WORKSTREAM" and self.owner_kind != "WORKSTREAM":
            raise ValueError("RESOURCE_WORKSTREAM_VISIBILITY_REQUIRES_OWNER")
        if any(not ref or len(ref) > 260 for ref in self.parent_refs):
            raise ValueError("RESOURCE_PARENT_REFERENCE_INVALID")
        if len(self.parent_refs) != len(set(self.parent_refs)):
            raise ValueError("RESOURCE_PARENT_REFERENCES_DUPLICATED")
        return self


class ResourceGrant(DomainModel):
    grant_id: str = Field(min_length=1, max_length=200)
    grantee_kind: GranteeKind
    grantee_ref: str = Field(min_length=1, max_length=160)
    capability: Literal["READ"] = "READ"
    state: Literal["ACTIVE", "REVOKED"] = "ACTIVE"
    granted_at: AwareDatetime
    revoked_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def revoked_is_explicit(self) -> ResourceGrant:
        if (self.state == "REVOKED") != (self.revoked_at is not None):
            raise ValueError("RESOURCE_GRANT_REVOCATION_TIME_INVALID")
        return self


class ResourceScopePolicy(DomainModel):
    default: ResourceScopeTemplate | None = None
    workstreams: dict[str, ResourceScopeTemplate] = Field(default_factory=dict)


class ResourceIntakeBasis(DomainModel):
    project_id: str
    project_revision: int = Field(ge=0)
    policy_id: str
    policy_digest: Sha256
    template: ResourceScopeTemplate
    parent_digests: dict[str, Sha256] = Field(default_factory=dict)
    actor_ref: str
    principal_digest: Sha256 | None = None
    session_ref: str | None = None
    role_assignment_ref: str | None = None
    prepared_at: AwareDatetime
    source_owned: Literal[True] | None = None


def resource_intake_basis_digest(basis: ResourceIntakeBasis) -> Sha256:
    payload = basis.model_dump(mode="python")
    if basis.source_owned is None:
        payload.pop("source_owned")
    return domain_digest("RESOURCE_INTAKE_BASIS", "1.0.0", canonical_payload(payload))


class ResourceScopeBody(ResourceScopeTemplate):
    access_basis: Literal["SOURCE_OWNER", "DERIVED_PARENTS", "PENDING_SOURCES"] | None = None
    scope_id: str
    project_id: str
    resource_ref: str = Field(min_length=1, max_length=260)
    revision: int = Field(ge=1)
    previous_digest: Sha256 | None = None
    grants: tuple[ResourceGrant, ...] = Field(default=(), max_length=256)
    event_kind: Literal[
        "ASSIGNED", "GRANTED", "REVOKED", "OWNER_CHANGED", "VISIBILITY_CHANGED", "LINEAGE_EXTENDED"
    ]
    actor_ref: str
    session_ref: str | None = None
    role_assignment_ref: str | None = None
    basis_digest: Sha256
    reason: str = Field(min_length=1, max_length=2_000)
    receipt_ref: str
    created_at: AwareDatetime
    schema_version: Literal["1.0.0"] = "1.0.0"


class ResourceScopeRecord(ResourceScopeBody):
    record_digest: Sha256

    @model_validator(mode="after")
    def sealed_body(self) -> ResourceScopeRecord:
        payload = self.model_dump(mode="python", exclude={"record_digest"})
        if self.access_basis is None:
            payload.pop("access_basis")
        if self.record_digest != domain_digest(
            "RESOURCE_SCOPE", "1.0.0", canonical_payload(payload)
        ):
            raise ValueError("RESOURCE_SCOPE_DIGEST_MISMATCH")
        if (self.revision == 1) != (self.previous_digest is None):
            raise ValueError("RESOURCE_SCOPE_REVISION_LINEAGE_INVALID")
        if self.resource_ref in self.parent_refs:
            raise ValueError("RESOURCE_SCOPE_SELF_PARENT")
        if len({item.grant_id for item in self.grants}) != len(self.grants):
            raise ValueError("RESOURCE_SCOPE_GRANT_IDS_DUPLICATED")
        return self


class ResourceScopeReceiptBody(DomainModel):
    receipt_id: str
    project_id: str
    resource_ref: str
    before_digest: Sha256 | None
    after_digest: Sha256
    event_kind: str
    actor_ref: str
    session_ref: str | None
    created_at: AwareDatetime
    semantic_truth_certified: Literal[False] = False
    schema_version: Literal["1.0.0"] = "1.0.0"


class ResourceScopeReceipt(ResourceScopeReceiptBody):
    receipt_digest: Sha256

    @model_validator(mode="after")
    def sealed_receipt(self) -> ResourceScopeReceipt:
        payload = self.model_dump(mode="python", exclude={"receipt_digest"})
        if self.receipt_digest != domain_digest(
            "RESOURCE_SCOPE_RECEIPT", "1.0.0", canonical_payload(payload)
        ):
            raise ValueError("RESOURCE_SCOPE_RECEIPT_DIGEST_MISMATCH")
        return self


class StagedResourceScope(DomainModel):
    basis: ResourceIntakeBasis
    record: ResourceScopeRecord
    receipt: ResourceScopeReceipt


class ResourceUse(DomainModel):
    project_id: str
    resource_ref: str
    capability: Literal["READ", "WRITE", "MANAGE"] = "READ"


class OperationResourceBindingBody(DomainModel):
    operation_id: str
    project_id: str
    request_digest: Sha256
    output_kind: Literal["RESULT", "ERROR"]
    output_digest: Sha256
    resource_uses: tuple[ResourceUse, ...]
    schema_version: Literal["1.0.0"] = "1.0.0"


class OperationResourceBinding(OperationResourceBindingBody):
    binding_digest: Sha256

    @model_validator(mode="after")
    def sealed_binding(self) -> OperationResourceBinding:
        if self.binding_digest != domain_digest(
            "OPERATION_RESOURCE_BINDING",
            "1.0.0",
            canonical_payload(self.model_dump(mode="python", exclude={"binding_digest"})),
        ):
            raise ValueError("OPERATION_RESOURCE_BINDING_DIGEST_MISMATCH")
        if any(use.project_id != self.project_id for use in self.resource_uses):
            raise ValueError("OPERATION_RESOURCE_BINDING_PROJECT_MISMATCH")
        return self


_RESOURCE_USES: ContextVar[dict[tuple[str, str, str], ResourceUse] | None] = ContextVar(
    "resource_uses",
    default=None,
)
_RESOURCE_PROJECT: ContextVar[str | None] = ContextVar("resource_project", default=None)


def record_resource_use(
    project_id: str, resource_ref: str, capability: Literal["READ", "WRITE", "MANAGE"]
) -> None:
    expected_project_id = _RESOURCE_PROJECT.get()
    if expected_project_id is not None and project_id != expected_project_id:
        raise ResourceScopeError("RESOURCE_SCOPE_PROJECT_MISMATCH")
    uses = _RESOURCE_USES.get()
    if uses is not None:
        if len(uses) >= 4096 and (project_id, resource_ref, capability) not in uses:
            raise ResourceScopeError("RESOURCE_SCOPE_USAGE_LIMIT")
        uses[(project_id, resource_ref, capability)] = ResourceUse(
            project_id=project_id,
            resource_ref=resource_ref,
            capability=capability,
        )


def current_resource_uses() -> tuple[ResourceUse, ...] | None:
    uses = _RESOURCE_USES.get()
    return None if uses is None else tuple(uses[key] for key in sorted(uses))


@contextmanager
def resource_use_scope(project_id: str | None = None) -> Generator[None]:
    parent = _RESOURCE_USES.get()
    parent_project_id = _RESOURCE_PROJECT.get()
    if parent_project_id is not None and project_id is not None and project_id != parent_project_id:
        raise ResourceScopeError("RESOURCE_SCOPE_PROJECT_MISMATCH")
    project_token = _RESOURCE_PROJECT.set(parent_project_id if project_id is None else project_id)
    uses: dict[tuple[str, str, str], ResourceUse] = {}
    token = _RESOURCE_USES.set(uses)
    try:
        yield
    finally:
        _RESOURCE_USES.reset(token)
        _RESOURCE_PROJECT.reset(project_token)
        if parent is not None:
            parent.update(uses)


@contextmanager
def resource_use_transaction() -> Generator[None]:
    """Discard dependencies of rolled-back staging, retaining the pre-transaction inputs."""
    uses = _RESOURCE_USES.get()
    before = None if uses is None else dict(uses)
    try:
        yield
    except BaseException:
        if uses is not None and before is not None:
            uses.clear()
            uses.update(before)
        raise


@contextmanager
def resource_use_verification(project_id: str) -> Generator[None]:
    """Bound a synchronous historical ACL recheck without re-consuming its sealed inputs.

    The same 4096-key guard runs against this verification ledger. Its entries are not
    new data reads and never merge into the surrounding query's still-active collector.
    """
    expected = _RESOURCE_PROJECT.get()
    if expected is not None and expected != project_id:
        raise ResourceScopeError("RESOURCE_SCOPE_PROJECT_MISMATCH")
    project_token = _RESOURCE_PROJECT.set(project_id)
    token = _RESOURCE_USES.set({})
    try:
        yield
    finally:
        _RESOURCE_USES.reset(token)
        _RESOURCE_PROJECT.reset(project_token)


def seal_resource_scope(body: ResourceScopeBody) -> ResourceScopeRecord:
    payload = body.model_dump(mode="python")
    if body.access_basis is None:
        payload.pop("access_basis")
    return ResourceScopeRecord.model_validate(
        {
            **payload,
            "record_digest": domain_digest("RESOURCE_SCOPE", "1.0.0", canonical_payload(payload)),
        }
    )


def seal_resource_scope_receipt(body: ResourceScopeReceiptBody) -> ResourceScopeReceipt:
    payload = body.model_dump(mode="python")
    return ResourceScopeReceipt.model_validate(
        {
            **payload,
            "receipt_digest": domain_digest(
                "RESOURCE_SCOPE_RECEIPT", "1.0.0", canonical_payload(payload)
            ),
        }
    )
