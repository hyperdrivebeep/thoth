from __future__ import annotations

from collections.abc import Callable
from typing import cast

from pydantic import AwareDatetime, Field, JsonValue

from thoth.application.services.public_web_policy import (
    normalize_public_web_update,
    reconcile_managed_web_permissions,
)
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ProjectLifecycle
from thoth.domain.governance import ProjectPolicy, ProjectReference, RoleAssignment
from thoth.domain.policy import (
    preferred_hosts_from_payload,
    public_web_enabled_from_payload,
    workspace_internet_grant_id_from_payload,
)
from thoth.domain.project import Project
from thoth.domain.public_web_access import PROJECT_PUBLIC_WEB_CONNECTOR_ID, PublicWebExecutionStatus
from thoth.domain.workspace_setup import WorkspaceSetupState
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.project import ProjectAlreadyExistsError, ProjectStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class ProjectCreateInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=10_000)
    cutoff_at: AwareDatetime
    overlay: str = Field(default="default", min_length=1, max_length=160)
    policy_binding_ref: str = Field(default="policy:default", min_length=1, max_length=160)


class ProjectReadInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class ProjectListInput(DomainModel):
    project_id: str = Field(default="system:projects", min_length=1, max_length=160)
    include_archived: bool = False


class ProjectExpectedRevisionInput(ProjectReadInput):
    expected_revision: int = Field(ge=0)


class ProjectMetadataUpdateInput(ProjectExpectedRevisionInput):
    name: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10_000)


class ProjectOverlayUpdateInput(ProjectExpectedRevisionInput):
    overlay: str = Field(min_length=1, max_length=160)


class ProjectPolicyUpdateInput(ProjectExpectedRevisionInput):
    payload: dict[str, JsonValue]


class ProjectRoleAssignInput(ProjectExpectedRevisionInput):
    actor_id: str = Field(min_length=1, max_length=160)
    organization_id: str | None = Field(default=None, max_length=160)
    role: str = Field(min_length=1, max_length=160)
    scope: str = Field(default="PROJECT", min_length=1, max_length=260)
    authority_tags: tuple[str, ...] = ()


class ProjectRoleRevokeInput(ProjectExpectedRevisionInput):
    role_assignment_id: str = Field(min_length=1, max_length=160)


class ProjectReferenceImportInput(ProjectExpectedRevisionInput):
    origin_project_id: str = Field(min_length=1, max_length=160)
    origin_revision: str = Field(min_length=1, max_length=160)
    rights_status: str = Field(min_length=1, max_length=80)
    scope: str = Field(min_length=1, max_length=260)


class ProjectCutoffImpactInput(ProjectReadInput):
    proposed_cutoff_at: AwareDatetime


class ProjectCutoffUpdateInput(ProjectExpectedRevisionInput):
    cutoff_at: AwareDatetime
    expected_impact_digest: str = Field(min_length=64, max_length=64)


class ProjectCommandHandlers:
    def __init__(
        self,
        *,
        store: ProjectStorePort,
        governance: GovernanceStorePort,
        artifacts: ArtifactLedgerPort,
        ledger: LedgerPort,
        clock: ClockPort,
        ids: IdGeneratorPort,
        default_policy_payload: dict[str, object] | None = None,
        workspace_setup: Callable[[], WorkspaceSetupState] | None = None,
        public_web_registered: Callable[[], bool] | None = None,
    ) -> None:
        self._store = store
        self._governance = governance
        self._artifacts = artifacts
        self._ledger = ledger
        self._clock = clock
        self._ids = ids
        self._workspace_setup = workspace_setup or WorkspaceSetupState
        self._public_web_registered = public_web_registered or (lambda: False)
        self._default_policy_payload = default_policy_payload or {
            "external_write": False,
            "physical_action": False,
            "unknown_action_tier": "R3",
            "connector_default": "DENY",
            "connector_allowlist": [],
            "connector_allowed_egress_classes": ["NONE"],
            "max_source_security_class": "INTERNAL",
            "sandbox_runtime_allowlist": [],
            "sandbox_network_policy": "DENY_ALL",
            "sandbox_allowed_hosts": [],
            "public_web": {
                "enabled": False,
                "preferred_hosts": [],
                "workspace_grant_id": None,
            },
        }

    async def create(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectCreateInput.model_validate(value)
        with self._ledger.transaction():
            policy_id = (
                f"policy:{request.project_id}:v1"
                if request.policy_binding_ref == "policy:default"
                else request.policy_binding_ref
            )
            project = Project(
                project_id=request.project_id,
                name=request.name,
                description=request.description,
                cutoff_at=request.cutoff_at,
                overlay=request.overlay,
                policy_binding_ref=policy_id,
            )
            try:
                self._store.create(project, created_at=self._clock.now().isoformat())
            except ProjectAlreadyExistsError as exc:
                raise RpcApplicationError(
                    RpcErrorCode.PROJECT_ALREADY_EXISTS,
                    "project already exists",
                    data={"project_id": request.project_id},
                ) from exc
            policy_payload = dict(self._default_policy_payload)
            digest = domain_digest(
                "PROJECT_POLICY",
                "1.0.0",
                canonical_payload(
                    {
                        "project_id": project.project_id,
                        "policy": policy_payload,
                    }
                ),
            )
            self._governance.put_policy(
                ProjectPolicy(
                    policy_id=policy_id,
                    project_id=project.project_id,
                    version=1,
                    payload=policy_payload,
                    policy_digest=digest,
                    created_at=self._clock.now(),
                )
            )
            return self._projection(project)

    async def list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectListInput.model_validate(value)
        authenticated = current_authenticated_actor()
        return {
            "projects": [
                self._projection(project)
                for project in self._store.list()
                if (
                    request.include_archived
                    or project.lifecycle != ProjectLifecycle.ARCHIVED_READ_ONLY
                )
                and (authenticated is None or project.project_id == authenticated.project_id)
            ]
        }

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectReadInput.model_validate(value)
        return self._projection(self._read(request.project_id))

    async def activate(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectExpectedRevisionInput.model_validate(value)
        with self._ledger.transaction():
            project = self._read(request.project_id)
            self._require_mutable(project)
            active_sources = tuple(
                item
                for item in self._governance.list_source_bindings(project.project_id)
                if item.state == "ACTIVE"
            )
            if not active_sources or self._governance.read_policy(project.project_id) is None:
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED,
                    "project activation requires a policy and at least one active source binding",
                )
            updated = project.model_copy(
                update={
                    "lifecycle": ProjectLifecycle.ACTIVE,
                    "source_binding_ids": tuple(item.binding_id for item in active_sources),
                    "revision": project.revision + 1,
                }
            )
            return self._update(updated, request.expected_revision)

    async def archive(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectExpectedRevisionInput.model_validate(value)
        with self._ledger.transaction():
            project = self._read(request.project_id)
            if project.lifecycle == ProjectLifecycle.ARCHIVED_READ_ONLY:
                return self._projection(project)
            updated = project.model_copy(
                update={
                    "lifecycle": ProjectLifecycle.ARCHIVED_READ_ONLY,
                    "revision": project.revision + 1,
                }
            )
            return self._update(updated, request.expected_revision)

    async def metadata_update(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectMetadataUpdateInput.model_validate(value)
        with self._ledger.transaction():
            project = self._read(request.project_id)
            self._require_mutable(project)
            updated = project.model_copy(
                update={
                    "name": request.name or project.name,
                    "description": (
                        project.description if request.description is None else request.description
                    ),
                    "revision": project.revision + 1,
                }
            )
            return self._update(updated, request.expected_revision)

    async def overlay_update(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectOverlayUpdateInput.model_validate(value)
        with self._ledger.transaction():
            project = self._read(request.project_id)
            self._require_mutable(project)
            updated = project.model_copy(
                update={"overlay": request.overlay, "revision": project.revision + 1}
            )
            return self._update(updated, request.expected_revision)

    async def policy_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectReadInput.model_validate(value)
        self._read(request.project_id)
        policy = self._governance.read_policy(request.project_id)
        if policy is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "project policy is missing")
        return {"policy": policy.model_dump(mode="json")}

    async def policy_update(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectPolicyUpdateInput.model_validate(value)
        with self._ledger.transaction():
            project = self._read(request.project_id)
            self._require_mutable(project)
            self._require_revision(project, request.expected_revision)
            current = self._governance.read_policy(project.project_id)
            version = 1 if current is None else current.version + 1
            payload = _merge_policy_payload(
                current.payload if current is not None else dict(self._default_policy_payload),
                {str(key): child for key, child in request.payload.items()},
                self._workspace_setup(),
                connector_registered=self._public_web_registered(),
            )
            digest = domain_digest(
                "PROJECT_POLICY",
                "1.0.0",
                canonical_payload(
                    {
                        "project_id": project.project_id,
                        "policy": payload,
                    }
                ),
            )
            policy = ProjectPolicy(
                policy_id=self._ids.new("policy"),
                project_id=project.project_id,
                version=version,
                payload=payload,
                policy_digest=digest,
                created_at=self._clock.now(),
            )
            self._governance.put_policy(policy)
            updated = project.model_copy(
                update={
                    "policy_binding_ref": policy.policy_id,
                    "revision": project.revision + 1,
                }
            )
            projection = self._update(updated, request.expected_revision)
            projection["policy"] = policy.model_dump(mode="json")
            return projection

    async def role_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectReadInput.model_validate(value)
        self._read(request.project_id)
        return {
            "roles": [
                role.model_dump(mode="json")
                for role in self._governance.list_roles(request.project_id)
            ]
        }

    async def role_assign(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectRoleAssignInput.model_validate(value)
        with self._ledger.transaction():
            project = self._read(request.project_id)
            self._require_mutable(project)
            self._require_revision(project, request.expected_revision)
            role = RoleAssignment(
                role_assignment_id=self._ids.new("role-assignment"),
                project_id=project.project_id,
                actor_id=request.actor_id,
                organization_id=request.organization_id,
                role=request.role,
                scope=request.scope,
                authority_tags=request.authority_tags,
                created_at=self._clock.now(),
            )
            self._governance.create_role(role)
            updated = project.model_copy(update={"revision": project.revision + 1})
            projection = self._update(updated, request.expected_revision)
            projection["role"] = role.model_dump(mode="json")
            return projection

    async def role_revoke(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectRoleRevokeInput.model_validate(value)
        with self._ledger.transaction():
            project = self._read(request.project_id)
            self._require_mutable(project)
            self._require_revision(project, request.expected_revision)
            try:
                role = self._governance.revoke_role(
                    project.project_id,
                    request.role_assignment_id,
                    revoked_at=self._clock.now().isoformat(),
                )
            except KeyError as exc:
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED, "active role assignment was not found"
                ) from exc
            updated = project.model_copy(update={"revision": project.revision + 1})
            projection = self._update(updated, request.expected_revision)
            projection["role"] = role.model_dump(mode="json")
            return projection

    async def reference_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectReadInput.model_validate(value)
        self._read(request.project_id)
        return {
            "references": [
                item.model_dump(mode="json")
                for item in self._governance.list_references(request.project_id)
            ]
        }

    async def reference_import(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectReferenceImportInput.model_validate(value)
        with self._ledger.transaction():
            project = self._read(request.project_id)
            self._require_mutable(project)
            self._require_revision(project, request.expected_revision)
            reference = ProjectReference(
                reference_id=self._ids.new("reference"),
                project_id=project.project_id,
                origin_project_id=request.origin_project_id,
                origin_revision=request.origin_revision,
                rights_status=request.rights_status,
                scope=request.scope,
                authority_status="EXTERNAL_REFERENCE",
                created_at=self._clock.now(),
            )
            self._governance.add_reference(reference)
            updated = project.model_copy(update={"revision": project.revision + 1})
            projection = self._update(updated, request.expected_revision)
            projection["reference"] = reference.model_dump(mode="json")
            return projection

    async def cutoff_impact(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectCutoffImpactInput.model_validate(value)
        project = self._read(request.project_id)
        return {"impact": self._cutoff_impact(project, request.proposed_cutoff_at)}

    def _cutoff_impact(
        self, project: Project, proposed_cutoff_at: AwareDatetime
    ) -> dict[str, JsonValue]:
        evidence = self._artifacts.list_evidence(project.project_id)
        affected_head_refs: list[JsonValue] = [
            str(key) for key in sorted(self._ledger.read_heads(project.project_id))
        ]
        payload: dict[str, JsonValue] = {
            "project_id": project.project_id,
            "current_cutoff_at": project.cutoff_at.isoformat(),
            "proposed_cutoff_at": proposed_cutoff_at.isoformat(),
            "current_evidence_count": len(evidence),
            "affected_head_refs": affected_head_refs,
            "requires_reingestion": True,
            "reason": (
                "source eligibility, sufficiency, hypotheses, actions, outcomes and memory "
                "must be recalculated against the proposed cutoff"
            ),
        }
        digest = domain_digest("CUTOFF_IMPACT", "1.0.0", canonical_payload(payload))
        return {**payload, "impact_digest": digest}

    async def cutoff_update(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectCutoffUpdateInput.model_validate(value)
        with self._ledger.transaction():
            project = self._read(request.project_id)
            self._require_mutable(project)
            impact_value = self._cutoff_impact(project, request.cutoff_at)
            if impact_value.get("impact_digest") != request.expected_impact_digest:
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED, "cutoff impact digest is stale or invalid"
                )
            updated = project.model_copy(
                update={"cutoff_at": request.cutoff_at, "revision": project.revision + 1}
            )
            projection = self._update(updated, request.expected_revision)
            projection["impact"] = impact_value
            return projection

    def _read(self, project_id: str) -> Project:
        project = self._store.read(project_id)
        if project is None:
            raise RpcApplicationError(
                RpcErrorCode.PROJECT_NOT_FOUND,
                "project was not found",
                data={"project_id": project_id},
            )
        return project

    def _update(self, project: Project, expected_revision: int) -> dict[str, JsonValue]:
        self._require_revision(project, expected_revision, updated=True)
        if not self._store.update(project, expected_revision=expected_revision):
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                "project revision changed concurrently",
                data={"expected_revision": expected_revision},
            )
        return self._projection(project)

    @staticmethod
    def _require_mutable(project: Project) -> None:
        if project.lifecycle == ProjectLifecycle.ARCHIVED_READ_ONLY:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                "ARCHIVED_READ_ONLY",
                data={"project_id": project.project_id},
            )

    @staticmethod
    def _require_revision(
        project: Project, expected_revision: int, *, updated: bool = False
    ) -> None:
        current = project.revision - 1 if updated else project.revision
        if current != expected_revision:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                "project revision does not match expected_revision",
                data={"expected_revision": expected_revision, "current_revision": current},
            )

    def _projection(self, project: Project) -> dict[str, JsonValue]:
        payload = project.model_dump(mode="json")
        payload["roles"] = [
            role.model_dump(mode="json") for role in self._governance.list_roles(project.project_id)
        ]
        payload["workstreams"] = [
            item.model_dump(mode="json")
            for item in self._governance.list_workstreams(project.project_id)
        ]
        payload["source_bindings"] = [
            item.model_dump(mode="json")
            for item in self._governance.list_source_bindings(project.project_id)
        ]
        payload["references"] = [
            item.model_dump(mode="json")
            for item in self._governance.list_references(project.project_id)
        ]
        policy = self._governance.read_policy(project.project_id)
        payload["policy"] = None if policy is None else policy.model_dump(mode="json")
        payload["working_heads"] = dict(self._ledger.read_heads(project.project_id))
        payload["public_web_execution"] = _public_web_execution(
            None if policy is None else policy.payload,
            self._workspace_setup(),
            self._public_web_registered(),
            None if policy is None else policy.version,
        ).model_dump(mode="json")
        return {str(key): child for key, child in payload.items()}


def _merge_policy_payload(
    current: dict[str, object],
    incoming: dict[str, object],
    workspace_setup: WorkspaceSetupState | None = None,
    *,
    connector_registered: bool = False,
) -> dict[str, object]:
    merged = dict(current)
    if "public_web" in incoming:
        web = normalize_public_web_update(incoming.get("public_web"))
        setup = workspace_setup or WorkspaceSetupState()
        if web.enabled:
            if setup.internet_consent != "ALLOWED" or not setup.internet_grant_id:
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED, "PUBLIC_WEB_CONSENT_REQUIRED"
                )
            if web.workspace_grant_id not in {None, setup.internet_grant_id}:
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "PUBLIC_WEB_GRANT_MISMATCH")
            web = web.model_copy(update={"workspace_grant_id": setup.internet_grant_id})
        remainder = {key: value for key, value in incoming.items() if key != "public_web"}
        if remainder:
            merged.update(remainder)
        return reconcile_managed_web_permissions(
            merged, web, connector_registered=connector_registered
        )
    preserved_web = incoming.get("public_web", merged.get("public_web"))
    merged.update(incoming)
    if "public_web" not in incoming and preserved_web is not None:
        merged["public_web"] = preserved_web
    return merged


def _public_web_execution(
    payload: dict[str, object] | None,
    setup: WorkspaceSetupState,
    connector_registered: bool,
    policy_revision: int | None,
) -> PublicWebExecutionStatus:
    enabled = False if payload is None else public_web_enabled_from_payload(payload)
    hosts = () if payload is None else preferred_hosts_from_payload(payload)
    grant = None if payload is None else workspace_internet_grant_id_from_payload(payload)
    allowlist = _string_tuple(None if payload is None else payload.get("connector_allowlist"))
    egress_value = None if payload is None else payload.get("connector_allowed_egress_classes")
    egress = _string_tuple(egress_value)
    reasons: list[str] = []
    grant_matches = bool(
        setup.internet_consent == "ALLOWED"
        and setup.internet_grant_id
        and grant == setup.internet_grant_id
    )
    if not enabled:
        return PublicWebExecutionStatus(
            desired_enabled=False,
            state="OFF",
            effective_hosts=hosts,
            policy_revision=policy_revision,
            workspace_consent=setup.internet_consent,
            workspace_grant_id=setup.internet_grant_id,
            grant_matches=grant_matches,
        )
    if setup.internet_consent != "ALLOWED":
        reasons.append("PUBLIC_WEB_CONSENT_REQUIRED")
    if not grant_matches:
        reasons.append("PUBLIC_WEB_GRANT_MISMATCH")
    if not hosts:
        reasons.append("PUBLIC_WEB_HOSTS_REQUIRED")
    if not connector_registered:
        reasons.append("PUBLIC_WEB_CONNECTOR_UNAVAILABLE")
    if PROJECT_PUBLIC_WEB_CONNECTOR_ID not in allowlist or "ALLOWLISTED_EXTERNAL" not in egress:
        reasons.append("PUBLIC_WEB_POLICY_INCONSISTENT")
    return PublicWebExecutionStatus(
        desired_enabled=True,
        state="BLOCKED" if reasons else "READY",
        reason_codes=tuple(reasons),
        managed_connector_ids=(PROJECT_PUBLIC_WEB_CONNECTOR_ID,) if connector_registered else (),
        effective_hosts=hosts,
        policy_revision=policy_revision,
        workspace_consent=setup.internet_consent,
        workspace_grant_id=setup.internet_grant_id,
        grant_matches=grant_matches,
    )


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    values = cast(list[object] | tuple[object, ...], value)
    return tuple(item for item in values if isinstance(item, str))
