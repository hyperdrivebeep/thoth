from __future__ import annotations

from typing import Literal

from pydantic import Field

from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.ids import ProjectId, Sha256
from thoth.domain.workspace_setup import InternetConsent

PROJECT_PUBLIC_WEB_CONNECTOR_ID = "project-public-web"
MANAGED_WEB_EGRESS_CLASS = "ALLOWLISTED_EXTERNAL"
MANAGED_WEB_OWNERSHIP_KEY = "managed_web_permissions"
MANAGED_WEB_SCHEMA_VERSION = 1

PublicWebExecutionState = Literal["OFF", "READY", "BLOCKED"]
PublicWebDisclosureKind = Literal[
    "REGISTERED_SITE_ENTRYPOINT",
    "OBSERVED_PUBLIC_DOCUMENT",
]
PublicWebRequestPurpose = Literal["SITE_DISCOVER", "READ"]
NormalizedHost = str


class ManagedWebPermissionOwnership(DomainModel):
    schema_version: int = MANAGED_WEB_SCHEMA_VERSION
    connector_ids: tuple[str, ...] = ()
    added_egress_classes: tuple[str, ...] = ()


class PublicWebUpdateInput(DomainModel):
    enabled: bool
    preferred_hosts: tuple[NormalizedHost, ...] = ()
    workspace_grant_id: str | None = None


class PublicWebAccessBasis(DomainModel):
    project_id: ProjectId
    policy_id: str = Field(min_length=1, max_length=160)
    policy_revision: int = Field(ge=1)
    policy_digest: Sha256
    workspace_grant_id: str
    workspace_revision: int = Field(ge=1)
    allowed_hosts: tuple[NormalizedHost, ...]
    hosts_digest: Sha256
    allowed_modes: tuple[PublicWebRequestPurpose, ...] = ("SITE_DISCOVER", "READ")

    @property
    def digest(self) -> Sha256:
        return domain_digest(
            "PUBLIC_WEB_ACCESS_BASIS",
            "1.0.0",
            canonical_payload(self.model_dump(mode="json")),
        )


class PublicWebRequestPlan(DomainModel):
    uri: str = Field(min_length=1, max_length=2_000)
    purpose: PublicWebRequestPurpose
    disclosure_kind: PublicWebDisclosureKind
    access_basis_digest: Sha256
    source_uri: str | None = None
    entrypoint_id: str | None = None


class PublicWebFailureDetail(DomainModel):
    stage: str = Field(min_length=1, max_length=80)
    connector_id: str | None = None
    error_code: str | None = None
    reason_code: str


class PublicWebExecutionStatus(DomainModel):
    desired_enabled: bool
    state: PublicWebExecutionState
    reason_codes: tuple[str, ...] = ()
    managed_connector_ids: tuple[str, ...] = ()
    effective_hosts: tuple[NormalizedHost, ...] = ()
    policy_revision: int | None = None
    workspace_consent: InternetConsent | None = None
    workspace_grant_id: str | None = None
    grant_matches: bool = False


def hosts_digest(hosts: tuple[str, ...]) -> Sha256:
    return domain_digest(
        "PUBLIC_WEB_HOSTS",
        "1.0.0",
        canonical_payload({"hosts": list(hosts)}),
    )
