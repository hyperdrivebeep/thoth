"""Explicit resource grants and current parent-intersection access checks."""

from __future__ import annotations

from thoth.application.services.resource_scope_context import current_resource_intake
from thoth.application.services.resource_scope_read_context import (
    ScopeReadContext,
    scope_read_context,
)
from thoth.domain.auth import AuthenticatedActorContext, current_authenticated_actor
from thoth.domain.canonical import canonical_payload, domain_digest, model_digest
from thoth.domain.governance import ProjectPolicy
from thoth.domain.operation import OperationRecord
from thoth.domain.resource_scope import (
    GranteeKind,
    ResourceGrant,
    ResourceIntakeBasis,
    ResourceScopeBody,
    ResourceScopeError,
    ResourceScopePolicy,
    ResourceScopeReceiptBody,
    ResourceScopeRecord,
    ResourceScopeTemplate,
    StagedResourceScope,
    record_resource_use,
    resource_intake_basis_digest,
    seal_resource_scope,
    seal_resource_scope_receipt,
)
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.auth import AuthSessionStorePort
from thoth.ports.evidence_graph import EvidenceGraphStorePort
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.project import ProjectStorePort
from thoth.ports.resource_scope import ResourceScopeStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


class ResourceScopeService:
    def __init__(
        self,
        *,
        store: ResourceScopeStorePort,
        artifacts: ArtifactLedgerPort,
        projects: ProjectStorePort,
        governance: GovernanceStorePort,
        sessions: AuthSessionStorePort,
        ledger: LedgerPort,
        clock: ClockPort,
        ids: IdGeneratorPort,
        evidence: EvidenceGraphStorePort | None = None,
    ) -> None:
        self._store = store
        self._artifacts = artifacts
        self._projects = projects
        self._governance = governance
        self._sessions = sessions
        self._ledger = ledger
        self._clock = clock
        self._ids = ids
        self._evidence = evidence

    def _actor(self, project_id: str) -> AuthenticatedActorContext | None:
        actor = current_authenticated_actor()
        if actor is None:
            return None
        session = self._sessions.read(actor.session_id)
        role = next(
            (
                r
                for r in self._governance.list_roles(project_id)
                if r.role_assignment_id == actor.role_assignment_id
            ),
            None,
        )
        if (
            actor.project_id != project_id
            or session is None
            or role is None
            or session.state != "ACTIVE"
            or session.expires_at <= self._clock.now()
            or session.project_id != project_id
            or session.actor_id != actor.actor_id
            or session.role_assignment_id != actor.role_assignment_id
            or session.role != actor.role
            or session.capabilities != actor.capabilities
            or session.data_scopes != actor.data_scopes
            or role.state != "ACTIVE"
            or role.actor_id != actor.actor_id
            or role.role != actor.role
            or (role.scope,) != actor.data_scopes
            or tuple(
                sorted(
                    tag.removeprefix("CAP_")
                    for tag in role.authority_tags
                    if tag.startswith("CAP_")
                )
            )
            != actor.capabilities
        ):
            raise ResourceScopeError("RESOURCE_AUTHORITY_CHANGED")
        return actor

    def _policy(self, project_id: str) -> ProjectPolicy:
        project = self._projects.read(project_id)
        policy = self._governance.read_policy(project_id)
        if project is None or policy is None or project.policy_binding_ref != policy.policy_id:
            raise ResourceScopeError("RESOURCE_POLICY_UNAVAILABLE")
        expected = domain_digest(
            "PROJECT_POLICY",
            "1.0.0",
            canonical_payload({"project_id": project_id, "policy": policy.payload}),
        )
        if expected != policy.policy_digest:
            raise ResourceScopeError("RESOURCE_POLICY_DIGEST_MISMATCH")
        return policy

    @staticmethod
    def _owns(scope: ResourceScopeTemplate, actor: AuthenticatedActorContext | None) -> bool:
        if actor is None:
            return scope.owner_kind == "PROJECT"
        if scope.owner_kind == "PROJECT":
            return "PROJECT" in actor.data_scopes and "ADMIN" in actor.capabilities
        return f"WORKSTREAM:{scope.owner_workstream}" in actor.data_scopes

    @staticmethod
    def _principal_digest(actor: AuthenticatedActorContext | None) -> str | None:
        return (
            None
            if actor is None
            else model_digest("RESOURCE_PRINCIPAL", actor, schema_version="1.0.0")
        )

    def prepare_intake(
        self, project_id: str, template: ResourceScopeTemplate | None = None
    ) -> ResourceIntakeBasis:
        inherited = current_resource_intake()
        if template is None and inherited is not None:
            if inherited.project_id != project_id:
                raise ResourceScopeError("RESOURCE_SCOPE_PROJECT_MISMATCH")
            self._validate_basis(inherited)
            return inherited
        actor = self._actor(project_id)
        policy = self._policy(project_id)
        if actor is not None and "WRITE" not in actor.capabilities:
            raise ResourceScopeError("RESOURCE_OWNER_ASSIGNMENT_DENIED")
        if template is None:
            config = ResourceScopePolicy.model_validate(
                policy.payload.get("resource_scope_policy", {})
            )
            matches = tuple(
                config.workstreams[scope.removeprefix("WORKSTREAM:")]
                for scope in (() if actor is None else actor.data_scopes)
                if scope.startswith("WORKSTREAM:")
                and scope.removeprefix("WORKSTREAM:") in config.workstreams
            )
            if len(matches) > 1:
                raise ResourceScopeError("RESOURCE_SCOPE_POLICY_AMBIGUOUS")
            template = matches[0] if matches else config.default
        if template is None:
            raise ResourceScopeError("RESOURCE_SCOPE_REQUIRED")
        admin = (
            actor is not None and "PROJECT" in actor.data_scopes and "ADMIN" in actor.capabilities
        )
        if not admin and not self._owns(template, actor):
            raise ResourceScopeError("RESOURCE_OWNER_ASSIGNMENT_DENIED")
        if (
            actor is not None
            and not admin
            and template.visibility == "PROJECT_SHARED"
            and "RESOURCE_SCOPE_WRITE" not in actor.capabilities
        ):
            raise ResourceScopeError("RESOURCE_SCOPE_MANAGEMENT_DENIED")
        parent_records = self._check_parent_basis(project_id, template.parent_refs)
        project = self._projects.read(project_id)
        assert project is not None
        return ResourceIntakeBasis(
            project_id=project_id,
            project_revision=project.revision,
            policy_id=policy.policy_id,
            policy_digest=policy.policy_digest,
            template=template,
            parent_digests={ref: parent_records[ref].record_digest for ref in template.parent_refs},
            actor_ref="local:operator" if actor is None else actor.actor_id,
            principal_digest=self._principal_digest(actor),
            session_ref=None if actor is None else actor.session_id,
            role_assignment_ref=None if actor is None else actor.role_assignment_id,
            prepared_at=self._clock.now(),
        )

    def _validate_basis(self, basis: ResourceIntakeBasis) -> None:
        actor = self._actor(basis.project_id)
        project = self._projects.read(basis.project_id)
        policy = self._policy(basis.project_id)
        if (
            project is None
            or project.revision != basis.project_revision
            or policy.policy_id != basis.policy_id
            or policy.policy_digest != basis.policy_digest
            or self._principal_digest(actor) != basis.principal_digest
        ):
            raise ResourceScopeError("RESOURCE_INTAKE_BASIS_CHANGED")
        parent_records = self._check_parent_basis(basis.project_id, tuple(basis.parent_digests))
        for parent, digest in basis.parent_digests.items():
            if parent_records[parent].record_digest != digest:
                raise ResourceScopeError("RESOURCE_PARENT_SCOPE_CHANGED")

    def stage(
        self, resource_ref: str, basis: ResourceIntakeBasis, *, derived: bool = False
    ) -> StagedResourceScope:
        self._validate_basis(basis)
        derived = derived and basis.source_owned is not True
        body = ResourceScopeBody(
            access_basis="DERIVED_PARENTS" if derived else "SOURCE_OWNER",
            **basis.template.model_dump(mode="python"),
            scope_id=self._ids.new("resource-scope"),
            project_id=basis.project_id,
            resource_ref=resource_ref,
            revision=1,
            grants=(),
            event_kind="ASSIGNED",
            actor_ref=basis.actor_ref,
            session_ref=basis.session_ref,
            role_assignment_ref=basis.role_assignment_ref,
            basis_digest=resource_intake_basis_digest(basis),
            reason="apply the explicit ingestion ownership scope",
            receipt_ref=self._ids.new("resource-scope-receipt"),
            created_at=self._clock.now(),
        )
        record = seal_resource_scope(body)
        return StagedResourceScope(basis=basis, record=record, receipt=self._receipt(record))

    def admit(self, value: StagedResourceScope | None) -> None:
        if value is None:
            raise ResourceScopeError("RESOURCE_SCOPE_REQUIRED")
        self._validate_basis(value.basis)
        if (
            value.record.project_id != value.basis.project_id
            or value.record.basis_digest != resource_intake_basis_digest(value.basis)
            or (value.basis.source_owned is True and value.record.access_basis != "SOURCE_OWNER")
            or ResourceScopeTemplate.model_validate(
                value.record.model_dump(
                    include={"owner_kind", "owner_workstream", "visibility", "parent_refs"}
                )
            )
            != value.basis.template
        ):
            raise ResourceScopeError("RESOURCE_INTAKE_RECORD_MISMATCH")
        self._store.append(value.record, value.receipt, expected_revision=0)
        actor = self._actor(value.record.project_id)
        record_resource_use(
            value.record.project_id,
            value.record.resource_ref,
            "WRITE"
            if value.record.access_basis == "DERIVED_PARENTS" or self._owns(value.record, actor)
            else "MANAGE",
        )

    @staticmethod
    def _receipt(record: ResourceScopeRecord):
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

    def _artifact_ref(self, project_id: str, resource_ref: str) -> str:
        artifact = self._artifacts.read_artifact(resource_ref)
        if artifact is None:
            span = self._artifacts.read_evidence(resource_ref)
            if span is not None and span.project_id == project_id:
                artifact = self._artifacts.read_artifact(span.artifact_id)
        if artifact is None and self._evidence is not None:
            source = self._evidence.read_source(resource_ref)
            if source is not None and source.project_id == project_id:
                artifact = self._artifacts.read_artifact(source.artifact_id)
        if artifact is None or artifact.project_id != project_id:
            raise ResourceScopeError("RESOURCE_REFERENCE_UNRESOLVED")
        return artifact.artifact_id

    def _required_record(
        self, project_id: str, resource_ref: str, context: ScopeReadContext | None = None
    ) -> ResourceScopeRecord:
        if context is not None:
            if context.project_id != project_id:
                raise ResourceScopeError("RESOURCE_SCOPE_PROJECT_MISMATCH")
            canonical = context.alias_to_canonical.get(resource_ref, resource_ref)
            if canonical in context.records:
                return context.records[canonical]
        value = self._store.read(project_id, resource_ref)
        if value is None:
            canonical = self._artifact_ref(project_id, resource_ref)
            value = None if context is None else context.records.get(canonical)
            if value is None:
                value = self._store.read(project_id, canonical)
            if value is None:
                raise ResourceScopeError("RESOURCE_SCOPE_UNKNOWN")
        if context is not None:
            context.alias_to_canonical[resource_ref] = value.resource_ref
            context.records[value.resource_ref] = value
        return value

    def _check_parent_basis(
        self, project_id: str, parents: tuple[str, ...]
    ) -> dict[str, ResourceScopeRecord]:
        actor = self._actor(project_id)
        if actor is not None and "READ" not in actor.capabilities:
            raise ResourceScopeError("RESOURCE_ACCESS_DENIED")
        context = scope_read_context(self, project_id, actor)
        records: dict[str, ResourceScopeRecord] = {}
        for parent in dict.fromkeys(parents):
            self._require_read(
                project_id, parent, actor, context.ancestors, context.checked, context
            )
            records[parent] = self._required_record(project_id, parent, context)
            record_resource_use(project_id, parent, "READ")
        return records

    def _derived_basis(
        self,
        project_id: str,
        parent_refs: tuple[str, ...],
        *,
        allow_empty: bool = False,
        source_owner_when_empty: bool = False,
    ) -> ResourceIntakeBasis:
        parents = tuple(dict.fromkeys(parent_refs))
        if not parents and not allow_empty:
            if source_owner_when_empty:
                return self.prepare_intake(project_id).model_copy(update={"source_owned": True})
            raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
        actor = self._actor(project_id)
        if actor is not None and "WRITE" not in actor.capabilities:
            raise ResourceScopeError("RESOURCE_WRITE_DENIED")
        policy = self._policy(project_id)
        project = self._projects.read(project_id)
        assert project is not None
        parent_records = self._check_parent_basis(project_id, parents)
        # Validate draft access before admitting its content into a source-bound
        # artifact. B follows actual sources, not the original draft's creator ACL.
        parents = tuple(
            ref for ref in parents if parent_records[ref].access_basis != "PENDING_SOURCES"
        )
        if not parents and not allow_empty:
            if source_owner_when_empty:
                return self.prepare_intake(project_id).model_copy(update={"source_owned": True})
            raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
        workstreams = tuple(
            scope.removeprefix("WORKSTREAM:")
            for scope in (() if actor is None else actor.data_scopes)
            if scope.startswith("WORKSTREAM:")
        )
        # These origin fields are provenance only for DERIVED_PARENTS, never a second ACL.
        template = ResourceScopeTemplate(
            owner_kind="WORKSTREAM" if len(workstreams) == 1 else "PROJECT",
            owner_workstream=workstreams[0] if len(workstreams) == 1 else None,
            visibility="WORKSTREAM" if len(workstreams) == 1 else "PROJECT_SHARED",
            parent_refs=parents,
        )
        return ResourceIntakeBasis(
            project_id=project_id,
            project_revision=project.revision,
            policy_id=policy.policy_id,
            policy_digest=policy.policy_digest,
            template=template,
            parent_digests={ref: parent_records[ref].record_digest for ref in parents},
            actor_ref="local:operator" if actor is None else actor.actor_id,
            principal_digest=self._principal_digest(actor),
            session_ref=None if actor is None else actor.session_id,
            role_assignment_ref=None if actor is None else actor.role_assignment_id,
            prepared_at=self._clock.now(),
        )

    def prepare_derived_intake(
        self, project_id: str, parent_refs: tuple[str, ...]
    ) -> ResourceIntakeBasis:
        with self._ledger.transaction():
            return self._derived_basis(project_id, parent_refs, source_owner_when_empty=True)

    def _is_derived(self, value: ResourceScopeRecord) -> bool:
        if value.access_basis is not None:
            return value.access_basis in {"DERIVED_PARENTS", "PENDING_SOURCES"}
        # Existing scoped graph records are classified from their actual typed owners.
        # Do not rewrite old hashes or classify arbitrary missing lineage as public.
        if self._artifacts.read_artifact(value.resource_ref) is not None:
            return False
        if self._evidence is not None:
            link = self._evidence.read_link(value.resource_ref)
            observation = self._evidence.read_observation(value.resource_ref)
            conflict = self._evidence.read_conflict(value.resource_ref)
            for record in (link, observation, conflict):
                if record is not None and record.project_id == value.project_id:
                    return True
            for ref in value.parent_refs:
                span = self._artifacts.read_evidence(ref)
                if (
                    span is not None
                    and span.project_id == value.project_id
                    and any(
                        item.correction_id == value.resource_ref
                        for item in self._evidence.list_span_corrections(value.project_id, ref)
                    )
                ):
                    return True
        raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")

    def ensure_derived(
        self,
        project_id: str,
        resource_ref: str,
        parent_refs: tuple[str, ...],
        *,
        is_new: bool,
    ) -> ResourceScopeRecord:
        with self._ledger.transaction():
            current = self._store.read(project_id, resource_ref)
            if current is None:
                if not is_new:
                    raise ResourceScopeError("RESOURCE_SCOPE_UNKNOWN")
                basis = self._derived_basis(project_id, parent_refs)
                staged = self.stage(resource_ref, basis, derived=True)
                self.admit(staged)
                return staged.record
            self.require_write(project_id, resource_ref)
            parents = tuple(dict.fromkeys((*current.parent_refs, *parent_refs)))
            self._check_parent_basis(project_id, parents)
            if parents == current.parent_refs:
                return current
            return self._change(
                current,
                self._actor(project_id),
                "retain the full derived source lineage",
                "LINEAGE_EXTENDED",
                current.grants,
                parent_refs=parents,
            )

    def record_revision_lineage(
        self, project_id: str, revision_digest: str, parents: tuple[str, ...]
    ) -> None:
        self._record_lineage(project_id, f"revision:{revision_digest}", parents)

    def record_receipt_lineage(
        self, project_id: str, digest: str, parents: tuple[str, ...]
    ) -> None:
        self._record_lineage(project_id, f"receipt:{digest}", parents)

    def record_control_lineage(
        self, project_id: str, digest: str, parents: tuple[str, ...]
    ) -> None:
        self._record_lineage(project_id, f"control:{digest}", parents)

    def _record_lineage(self, project_id: str, reference: str, parents: tuple[str, ...]) -> None:
        if self._store.read(project_id, reference) is not None:
            return
        retained: list[str] = []
        for parent in dict.fromkeys(parents):
            if parent == reference:
                continue
            known = self._store.read(project_id, parent)
            if known is not None and known.access_basis == "PENDING_SOURCES":
                continue  # Known source-free draft content is included once actual sources bind it.
            retained.append(parent)
        basis = self._derived_basis(project_id, tuple(retained), allow_empty=True)
        staged = self.stage(reference, basis, derived=True)
        if not retained:
            body = ResourceScopeBody.model_validate(
                {
                    **staged.record.model_dump(exclude={"record_digest"}),
                    "access_basis": "PENDING_SOURCES",
                    "reason": "retain a source-free input draft without granting shared access",
                }
            )
            record = seal_resource_scope(body)
            self._store.append(record, self._receipt(record), expected_revision=0)
        else:
            self.admit(staged)

    def require_revision(self, project_id: str, revision_digest: str) -> None:
        self.require_read(project_id, f"revision:{revision_digest}")

    def require_admitted_revision(self, project_id: str, revision_digest: str) -> None:
        """Read proven draft provenance already admitted into a source-bound record.

        Admission must first use require_revision; unknown legacy is never exempted.
        A source-free draft adds provenance, not a separate ACL or a new shared resource.
        """
        with self._ledger.transaction():
            actor = self._actor(project_id)
            if actor is not None and "READ" not in actor.capabilities:
                raise ResourceScopeError("RESOURCE_ACCESS_DENIED")
            value = self._required_record(project_id, f"revision:{revision_digest}")
            if value.access_basis != "PENDING_SOURCES":
                self.require_revision(project_id, revision_digest)

    def may_read_revision(self, project_id: str, revision_digest: str) -> bool:
        return self.may_read(project_id, f"revision:{revision_digest}")

    def require_read(self, project_id: str, resource_ref: str) -> None:
        self.require_reads(project_id, (resource_ref,))

    def require_reads(self, project_id: str, resource_refs: tuple[str, ...]) -> None:
        with self._ledger.transaction():
            self._check_reads(project_id, resource_refs)

    def _check_reads(self, project_id: str, resource_refs: tuple[str, ...]) -> None:
        self._check_parent_basis(project_id, resource_refs)

    def _require_read(
        self,
        project_id: str,
        resource_ref: str,
        actor: AuthenticatedActorContext | None,
        ancestors: set[str],
        checked: set[str],
        context: ScopeReadContext | None = None,
    ) -> None:
        if context is None:
            context = ScopeReadContext(project_id, actor, checked=checked, ancestors=ancestors)
        if resource_ref in checked:
            return
        value = self._required_record(project_id, resource_ref, context)
        if value.resource_ref in ancestors or len(ancestors) >= 128:
            raise ResourceScopeError("RESOURCE_SCOPE_LINEAGE_INVALID")
        if value.resource_ref in checked:
            checked.add(resource_ref)
            return
        derived = self._is_derived(value)
        if value.access_basis == "PENDING_SOURCES":
            creator = "local:operator" if actor is None else actor.actor_id
            if creator != value.actor_ref:
                raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
            checked.add(value.resource_ref)
            checked.add(resource_ref)
            return  # Draft management only; this marker never authorizes another actor.
        if derived and not value.parent_refs:
            raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
        allowed = derived or self._owns(value, actor) or value.visibility == "PROJECT_SHARED"
        if not derived and actor is not None and value.visibility == "EXPLICIT_GRANT":
            allowed = allowed or any(
                grant.state == "ACTIVE"
                and (
                    (grant.grantee_kind == "ACTOR" and grant.grantee_ref == actor.actor_id)
                    or (
                        grant.grantee_kind == "WORKSTREAM"
                        and f"WORKSTREAM:{grant.grantee_ref}" in actor.data_scopes
                    )
                )
                for grant in value.grants
            )
        if not allowed:
            raise ResourceScopeError("RESOURCE_ACCESS_DENIED")
        ancestors.add(value.resource_ref)
        for parent in value.parent_refs:
            self._require_read(project_id, parent, actor, ancestors, checked, context)
        ancestors.remove(value.resource_ref)
        checked.add(value.resource_ref)
        checked.add(resource_ref)

    def may_read(self, project_id: str, resource_ref: str) -> bool:
        with self._ledger.transaction():
            try:
                self._check_reads(project_id, (resource_ref,))
            except ResourceScopeError as exc:
                if exc.code in {
                    "RESOURCE_ACCESS_DENIED",
                    "RESOURCE_SCOPE_UNKNOWN",
                    "RESOURCE_REFERENCE_UNRESOLVED",
                    "RESOURCE_LINEAGE_UNKNOWN",
                }:
                    # A normal filter result must not cross a nested transaction as
                    # a failure. Unexpected errors still invalidate the parent UoW.
                    return False
                raise
            return True

    def require_write(self, project_id: str, resource_ref: str) -> None:
        with self._ledger.transaction():
            self._check_write(project_id, resource_ref)
            record_resource_use(project_id, resource_ref, "WRITE")

    def _check_write(self, project_id: str, resource_ref: str) -> None:
        actor = self._actor(project_id)
        context = scope_read_context(self, project_id, actor)
        value = self._required_record(project_id, resource_ref, context)
        if (not self._is_derived(value) and not self._owns(value, actor)) or (
            actor is not None and "WRITE" not in actor.capabilities
        ):
            raise ResourceScopeError("RESOURCE_WRITE_DENIED")
        self._require_read(
            project_id, resource_ref, actor, context.ancestors, context.checked, context
        )

    def may_write(self, project_id: str, resource_ref: str) -> bool:
        with self._ledger.transaction():
            try:
                self._check_write(project_id, resource_ref)
            except ResourceScopeError:
                return False
            return True

    def require_operation(self, operation: OperationRecord) -> None:
        if operation.result is None and operation.error is None:
            return
        if operation.resource_uses is None:
            raise ResourceScopeError("RESOURCE_SCOPE_UNKNOWN")
        with self._ledger.transaction():
            self._actor(operation.project_id)
            if any(use.project_id != operation.project_id for use in operation.resource_uses):
                raise ResourceScopeError("RESOURCE_SCOPE_PROJECT_MISMATCH")
            self.require_reads(
                operation.project_id,
                tuple(
                    use.resource_ref for use in operation.resource_uses if use.capability == "READ"
                ),
            )
            for use in operation.resource_uses:
                if use.capability == "READ":
                    continue
                elif use.capability == "WRITE":
                    self.require_write(use.project_id, use.resource_ref)
                else:
                    self._require_manage(self._required_record(use.project_id, use.resource_ref))

    def verify_historical_operation_access(self, operation: OperationRecord) -> None:
        from thoth.application.services.historical_access_verification import (
            historical_verification_allowed,
        )
        from thoth.domain.resource_scope import resource_use_verification

        if not historical_verification_allowed():
            self.require_operation(operation)
            return
        with resource_use_verification(operation.project_id):
            self.require_operation(operation)

    def _require_manage(
        self, value: ResourceScopeRecord, *, write: bool = False
    ) -> AuthenticatedActorContext | None:
        actor = self._actor(value.project_id)
        if self._is_derived(value):
            raise ResourceScopeError("DERIVED_ACCESS_FOLLOWS_SOURCES")
        if write and actor is not None and "WRITE" not in actor.capabilities:
            raise ResourceScopeError("RESOURCE_SCOPE_MANAGEMENT_DENIED")
        if actor is None:
            allowed = value.owner_kind == "PROJECT"
        else:
            allowed = "PROJECT" in actor.data_scopes and "ADMIN" in actor.capabilities
            allowed = allowed or (
                self._owns(value, actor) and "RESOURCE_SCOPE_WRITE" in actor.capabilities
            )
        if not allowed:
            raise ResourceScopeError("RESOURCE_SCOPE_MANAGEMENT_DENIED")
        record_resource_use(value.project_id, value.resource_ref, "MANAGE")
        return actor

    def read_scope(self, project_id: str, resource_ref: str) -> ResourceScopeRecord:
        with self._ledger.transaction():
            value = self._required_record(project_id, resource_ref)
            if self._is_derived(value):
                self.require_read(project_id, resource_ref)
            else:
                self._require_manage(value)
        return value

    def _require_project_admin(self, project_id: str) -> None:
        actor = self._actor(project_id)
        if actor is not None and not (
            "PROJECT" in actor.data_scopes and {"ADMIN", "WRITE"}.issubset(actor.capabilities)
        ):
            raise ResourceScopeError("RESOURCE_SCOPE_MANAGEMENT_DENIED")

    def _known_owner(self, project_id: str, template: ResourceScopeTemplate) -> None:
        if template.owner_kind == "WORKSTREAM" and not any(
            role.state == "ACTIVE" and role.scope == f"WORKSTREAM:{template.owner_workstream}"
            for role in self._governance.list_roles(project_id)
        ):
            raise ResourceScopeError("RESOURCE_OWNER_WORKSTREAM_UNKNOWN")

    def assign_legacy(
        self,
        project_id: str,
        resource_ref: str,
        *,
        expected_revision: int,
        template: ResourceScopeTemplate,
        reason: str,
    ) -> ResourceScopeRecord:
        with self._ledger.transaction():
            self._require_project_admin(project_id)
            if self._artifact_ref(project_id, resource_ref) != resource_ref:
                raise ResourceScopeError("RESOURCE_PARENT_ASSIGNMENT_REQUIRED")
            if expected_revision != 0 or self._store.read(project_id, resource_ref) is not None:
                raise ResourceScopeError("RESOURCE_SCOPE_REVISION_CONFLICT")
            self._known_owner(project_id, template)
            basis = self.prepare_intake(project_id, template)
            staged = self.stage(resource_ref, basis)
            body = ResourceScopeBody.model_validate(
                {
                    **staged.record.model_dump(exclude={"record_digest"}),
                    "reason": reason,
                }
            )
            record = seal_resource_scope(body)
            staged = StagedResourceScope(basis=basis, record=record, receipt=self._receipt(record))
            self.admit(staged)
            return record

    def update_scope(
        self,
        project_id: str,
        resource_ref: str,
        *,
        expected_revision: int,
        template: ResourceScopeTemplate,
        reason: str,
    ) -> ResourceScopeRecord:
        with self._ledger.transaction():
            current = self._required_record(project_id, resource_ref)
            actor = self._require_manage(current, write=True)
            if current.resource_ref != resource_ref or current.revision != expected_revision:
                raise ResourceScopeError("RESOURCE_SCOPE_REVISION_CONFLICT")
            if current.parent_refs != template.parent_refs:
                raise ResourceScopeError("RESOURCE_PARENT_SCOPE_IMMUTABLE")
            moved = (current.owner_kind, current.owner_workstream) != (
                template.owner_kind,
                template.owner_workstream,
            )
            if moved:
                try:
                    self._require_project_admin(project_id)
                except ResourceScopeError as exc:
                    raise ResourceScopeError("RESOURCE_OWNER_TRANSFER_DENIED") from exc
            self._known_owner(project_id, template)
            if not moved and current.visibility == template.visibility:
                return current
            grants = current.grants
            if template.visibility == "WORKSTREAM":
                grants = tuple(
                    g.model_copy(update={"state": "REVOKED", "revoked_at": self._clock.now()})
                    if g.state == "ACTIVE"
                    else g
                    for g in grants
                )
            return self._change(
                current,
                actor,
                reason,
                "OWNER_CHANGED" if moved else "VISIBILITY_CHANGED",
                grants,
                template=template,
            )

    def grant(
        self,
        project_id: str,
        resource_ref: str,
        *,
        expected_revision: int,
        grantee_kind: GranteeKind,
        grantee_ref: str,
        reason: str,
    ) -> ResourceScopeRecord:
        with self._ledger.transaction():
            current = self._required_record(project_id, resource_ref)
            actor = self._require_manage(current, write=True)
            if current.resource_ref != resource_ref or current.revision != expected_revision:
                raise ResourceScopeError("RESOURCE_SCOPE_REVISION_CONFLICT")
            roles = self._governance.list_roles(project_id)
            if not any(
                role.state == "ACTIVE"
                and (
                    role.actor_id == grantee_ref
                    if grantee_kind == "ACTOR"
                    else role.scope == f"WORKSTREAM:{grantee_ref}"
                )
                for role in roles
            ):
                raise ResourceScopeError("RESOURCE_GRANTEE_NOT_IN_PROJECT")
            if any(
                g.state == "ACTIVE"
                and (g.grantee_kind, g.grantee_ref) == (grantee_kind, grantee_ref)
                for g in current.grants
            ):
                return current
            grant = ResourceGrant(
                grant_id=self._ids.new("resource-grant"),
                grantee_kind=grantee_kind,
                grantee_ref=grantee_ref,
                granted_at=self._clock.now(),
            )
            return self._change(current, actor, reason, "GRANTED", (*current.grants, grant))

    def revoke(
        self,
        project_id: str,
        resource_ref: str,
        *,
        expected_revision: int,
        grant_id: str,
        reason: str,
    ) -> ResourceScopeRecord:
        with self._ledger.transaction():
            current = self._required_record(project_id, resource_ref)
            actor = self._require_manage(current, write=True)
            if current.resource_ref != resource_ref or current.revision != expected_revision:
                raise ResourceScopeError("RESOURCE_SCOPE_REVISION_CONFLICT")
            grant = next((g for g in current.grants if g.grant_id == grant_id), None)
            if grant is None:
                raise ResourceScopeError("RESOURCE_GRANT_NOT_FOUND")
            if grant.state == "REVOKED":
                return current
            changed = grant.model_copy(update={"state": "REVOKED", "revoked_at": self._clock.now()})
            grants = tuple(changed if g.grant_id == grant_id else g for g in current.grants)
            return self._change(current, actor, reason, "REVOKED", grants)

    def _change(
        self,
        current: ResourceScopeRecord,
        actor: AuthenticatedActorContext | None,
        reason: str,
        event_kind: str,
        grants: tuple[ResourceGrant, ...],
        *,
        parent_refs: tuple[str, ...] | None = None,
        template: ResourceScopeTemplate | None = None,
    ) -> ResourceScopeRecord:
        policy = self._policy(current.project_id)
        payload = current.model_dump(mode="python", exclude={"record_digest"})
        payload.update(
            {
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
                "basis_digest": domain_digest(
                    "RESOURCE_SCOPE_CHANGE_BASIS",
                    "1.0.0",
                    canonical_payload(
                        {
                            "policy_digest": policy.policy_digest,
                            "principal_digest": self._principal_digest(actor),
                            "before_digest": current.record_digest,
                        }
                    ),
                ),
                "receipt_ref": self._ids.new("resource-scope-receipt"),
                "created_at": self._clock.now(),
                "reason": reason,
            }
        )
        if parent_refs is not None:
            payload["parent_refs"] = parent_refs
        if template is not None:
            payload.update(template.model_dump(mode="python"))
        record = seal_resource_scope(ResourceScopeBody.model_validate(payload))
        self._store.append(record, self._receipt(record), expected_revision=current.revision)
        return record
