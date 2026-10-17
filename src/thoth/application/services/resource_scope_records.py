"""Scope record construction only; authority, reads, writes, IDs and clocks stay in the service."""

from __future__ import annotations

from typing import Literal, TypedDict

from thoth.domain.auth import AuthenticatedActorContext
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.governance import ProjectPolicy
from thoth.domain.project import Project
from thoth.domain.resource_scope import (
    ResourceGrant,
    ResourceIntakeBasis,
    ResourceScopeBody,
    ResourceScopeReceipt,
    ResourceScopeReceiptBody,
    ResourceScopeRecord,
    ResourceScopeTemplate,
    resource_intake_basis_digest,
    seal_resource_scope,
    seal_resource_scope_receipt,
)


class IntakeIdentityFields(TypedDict):
    project_id: str
    project_revision: int
    policy_id: str
    policy_digest: str
    template: ResourceScopeTemplate


class InitialScopeFields(TypedDict):
    project_id: str
    resource_ref: str
    revision: int
    grants: tuple[ResourceGrant, ...]
    event_kind: Literal["ASSIGNED"]
    actor_ref: str
    session_ref: str | None
    role_assignment_ref: str | None
    basis_digest: str
    reason: str


def policy_content_digest(project_id: str, policy: ProjectPolicy) -> str:
    return domain_digest(
        "PROJECT_POLICY",
        "1.0.0",
        canonical_payload({"project_id": project_id, "policy": policy.payload}),
    )


def intake_identity_fields(
    project_id: str, project: Project, policy: ProjectPolicy, template: ResourceScopeTemplate
) -> IntakeIdentityFields:
    return {
        "project_id": project_id,
        "project_revision": project.revision,
        "policy_id": policy.policy_id,
        "policy_digest": policy.policy_digest,
        "template": template,
    }


def initial_scope_fields(resource_ref: str, basis: ResourceIntakeBasis) -> InitialScopeFields:
    return {
        "project_id": basis.project_id,
        "resource_ref": resource_ref,
        "revision": 1,
        "grants": (),
        "event_kind": "ASSIGNED",
        "actor_ref": basis.actor_ref,
        "session_ref": basis.session_ref,
        "role_assignment_ref": basis.role_assignment_ref,
        "basis_digest": resource_intake_basis_digest(basis),
        "reason": "apply the explicit ingestion ownership scope",
    }


def scope_receipt(record: ResourceScopeRecord) -> ResourceScopeReceipt:
    return seal_resource_scope_receipt(
        ResourceScopeReceiptBody(
            receipt_id=record.receipt_ref,
            project_id=record.project_id,
            resource_ref=record.resource_ref,
            before_digest=record.previous_digest,
            after_digest=record.record_digest,
            event_kind=record.event_kind,
            actor_ref=record.actor_ref,
            session_ref=record.session_ref,
            created_at=record.created_at,
        )
    )


def pending_source_record(record: ResourceScopeRecord) -> ResourceScopeRecord:
    body = ResourceScopeBody.model_validate(
        {
            **record.model_dump(exclude={"record_digest"}),
            "access_basis": "PENDING_SOURCES",
            "reason": "retain a source-free input draft without granting shared access",
        }
    )
    return seal_resource_scope(body)


def assigned_scope_record(record: ResourceScopeRecord, reason: str) -> ResourceScopeRecord:
    body = ResourceScopeBody.model_validate(
        {
            **record.model_dump(exclude={"record_digest"}),
            "reason": reason,
        }
    )
    return seal_resource_scope(body)


def change_fields(
    current: ResourceScopeRecord,
    actor: AuthenticatedActorContext | None,
    event_kind: str,
    grants: tuple[ResourceGrant, ...],
) -> dict[str, object]:
    return {
        "revision": current.revision + 1,
        "previous_digest": current.record_digest,
        "event_kind": event_kind,
        "grants": grants,
        "visibility": "EXPLICIT_GRANT"
        if current.visibility == "WORKSTREAM" and event_kind == "GRANTED"
        else current.visibility,
        "actor_ref": "local:operator" if actor is None else actor.actor_id,
        "session_ref": None if actor is None else actor.session_id,
        "role_assignment_ref": None if actor is None else actor.role_assignment_id,
    }


def change_basis_digest(
    policy_digest: str, principal_digest: str | None, before_digest: str
) -> str:
    return domain_digest(
        "RESOURCE_SCOPE_CHANGE_BASIS",
        "1.0.0",
        canonical_payload(
            {
                "policy_digest": policy_digest,
                "principal_digest": principal_digest,
                "before_digest": before_digest,
            }
        ),
    )
