"""One no-I/O plan for preview and apply, bound to exact authority and dependencies."""

from dataclasses import dataclass
from typing import cast

from thoth.application.services.research_history_scope import actor_scope_basis, actor_scope_digest
from thoth.application.services.restore_impact import RestoreImpactPlanner
from thoth.application.services.restore_members import require_member_basis, require_plan_graph
from thoth.application.services.restore_profiles import semantic_groups
from thoth.application.services.restore_source_basis import content_origin
from thoth.application.services.revision_diff import semantic_diff
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.research_history import HistoryCapability
from thoth.domain.resource_scope import ResourceScopeError
from thoth.domain.restore import RestoreBasis, RestoreError, RestorePreview, RestoreSelection
from thoth.domain.revision import EntitySnapshot, SemanticRevision
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.baseline import BaselineStorePort
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.execution import ExecutionStorePort
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.project import ProjectStorePort
from thoth.ports.research_history import ResearchHistoryReadPort
from thoth.ports.resource_scope import ResourceScopeStorePort, ResourceWriteAccessPort
from thoth.ports.restore import (
    RestoreProfileRegistryPort,
    RestoreReferenceResolverPort,
    RestoreSourceResolverPort,
)
from thoth.ports.thread import ThreadStorePort


@dataclass(frozen=True)
class RestorePlan:
    basis: RestoreBasis
    preview: RestorePreview
    target: SemanticRevision
    snapshot: EntitySnapshot


class RestorePlanner:
    def __init__(
        self,
        *,
        ledger: LedgerPort,
        profiles: RestoreProfileRegistryPort,
        impact: RestoreImpactPlanner,
        access: ResourceWriteAccessPort,
        projects: ProjectStorePort,
        governance: GovernanceStorePort,
        artifacts: ArtifactLedgerPort,
        scopes: ResourceScopeStorePort,
        threads: ThreadStorePort,
        executions: ExecutionStorePort,
        controls: ControlRecordStorePort,
        baselines: BaselineStorePort,
        history: ResearchHistoryReadPort,
        sources: RestoreSourceResolverPort,
        references: RestoreReferenceResolverPort,
        apply_ready: bool = False,
    ) -> None:
        self.ledger, self.profiles, self.impact, self.access = ledger, profiles, impact, access
        self.baselines = baselines
        self.history = history
        self.sources = sources
        self.references = references
        self.projects, self.governance, self.artifacts, self.scopes = (
            projects,
            governance,
            artifacts,
            scopes,
        )
        self.threads, self.executions, self.controls, self.apply_ready = (
            threads,
            executions,
            controls,
            apply_ready,
        )

    def build(self, selection: RestoreSelection, *, require_write: bool = False) -> RestorePlan:
        project = selection.project_id
        self.access.require_reads(project, ())
        actor = current_authenticated_actor()
        writable = actor is None or (
            actor.project_id == project and {"WRITE", "REVISION"}.issubset(actor.capabilities)
        )
        if (
            require_write
            and actor is not None
            and (
                actor.project_id != project
                or not {"WRITE", "REVISION"}.issubset(actor.capabilities)
            )
        ):
            raise RestoreError("RESTORE_ACCESS_DENIED")
        key = f"{selection.entity_type.value}:{selection.entity_id}"
        heads = self.ledger.read_heads(project)
        if heads.get(key) != selection.expected_current_head:
            raise RestoreError("RESTORE_HEAD_CHANGED")
        target, snapshot = self.read_revision(project, selection.target_revision_digest)
        current, current_snapshot = self.read_revision(project, selection.expected_current_head)
        if any(
            (r.entity_type, r.entity_id) != (selection.entity_type, selection.entity_id)
            for r in (target, current)
        ):
            raise RestoreError("RESTORE_TARGET_MISMATCH")
        profile, record = self.profiles.resolve(target, snapshot)
        origin = content_origin(self.ledger, target, snapshot)
        self.access.require_revision(project, origin.revision_digest)
        current_profile, current_record = self.profiles.resolve(current, current_snapshot)
        if profile.profile_id != current_profile.profile_id:
            raise RestoreError("RESTORE_SCHEMA_UNSUPPORTED")
        object_id = profile.object_id(record)
        if current_profile.object_id(current_record) != object_id:
            raise RestoreError("RESTORE_TARGET_MISMATCH")
        require_plan_graph(record)
        impact = self.impact.build(project, key, object_id)
        expected = dict(impact.checked_heads)
        writable = writable and all(
            self.access.may_write(project, f"revision:{digest}") for digest in expected.values()
        )
        if require_write:
            try:
                for digest in expected.values():
                    self.access.require_write(project, f"revision:{digest}")
            except ResourceScopeError as exc:
                raise RestoreError("RESTORE_DEPENDENT_SCOPE_DENIED") from exc
        object_key = f"DECISION_OBJECT:{object_id}"
        if object_key not in heads:
            raise RestoreError("RESTORE_IMPACT_INCOMPLETE")
        expected[object_key] = heads[object_key]
        self.access.require_revision(project, heads[object_key])
        for reference in profile.references(record):
            if reference not in heads:
                raise RestoreError("RESTORE_MEMBERS_REQUIRE_REVIEW")
            member, member_snapshot = self.read_revision(project, heads[reference])
            member_profile, member_record = self.profiles.resolve(member, member_snapshot)
            if member_profile.object_id(member_record) != object_id:
                raise RestoreError("RESTORE_TARGET_MISMATCH")
            expected[reference] = member.revision_digest
        require_member_basis(self.ledger, origin, record, profile.references(record), expected)
        reference_basis = self.references.resolve(origin, record)
        expected.update(reference_basis.expected_heads)
        project_record, policy = self.projects.read(project), self.governance.read_policy(project)
        if project_record is None or policy is None:
            raise RestoreError("RESTORE_ACCESS_DENIED")
        if str(project_record.lifecycle) in {"ARCHIVED", "ARCHIVED_READ_ONLY"}:
            raise RestoreError("RESTORE_PROTECTED_STATE")
        cutoff = record.model_dump(mode="python").get("cutoff_at")
        if cutoff is not None and cutoff != project_record.cutoff_at:
            raise RestoreError("RESTORE_SOURCE_DRIFT")
        source_refs = profile.evidence_refs(record)
        frozen_sources = self.sources.resolve(project, origin.revision_digest, source_refs)
        source_basis = self._sources(project, source_refs, frozen_sources)
        consumer_basis = {**impact.consumer_basis, **self._active_consumers(project, object_id)}
        consumer_basis["typed_references"] = reference_basis.model_dump(mode="python")
        consumer_basis["baselines"] = {
            item.baseline_set_id: item.model_dump(mode="json")
            for item in self.baselines.list_sets(project)
            if set(item.head_map).intersection(expected)
        }
        authority = actor_scope_basis(project)
        authority["resource_scopes"] = self._scope_basis(
            project, tuple(expected.values()), tuple(source_basis)
        )
        states = self.ledger.read_dependency_states(project)
        basis = RestoreBasis(
            selection=selection,
            profile_id=profile.profile_id,
            target_content_digest=snapshot.content_digest,
            expected_heads=expected,
            dependency_states={key: states[key].value for key in expected if key in states},
            source_basis=source_basis,
            policy_basis={
                "policy_digest": policy.policy_digest,
                "cutoff_at": project_record.cutoff_at.isoformat(),
                "policy_ref": project_record.policy_binding_ref,
            },
            authority_basis=authority,
            consumer_basis=consumer_basis,
            impact=impact.plan,
            impact_complete=True,
        )
        digest = domain_digest("RESTORE_BASIS", "1.0.0", canonical_payload(basis))
        changes = semantic_diff(current_snapshot.content, snapshot.content)
        preview = RestorePreview(
            selection=selection,
            profile_id=profile.profile_id,
            availability="NO_CHANGE" if not changes else "AVAILABLE",
            reason_codes=(),
            capability=HistoryCapability(
                preview_supported=True,
                restore="RESTORE_SUPPORTED" if self.apply_ready else "READ_ONLY",
                apply_ready=self.apply_ready and writable,
                reason_codes=("RESTORE_ACCESS_DENIED",)
                if not writable
                else ()
                if self.apply_ready
                else ("RESTORE_NOT_READY",),
            ),
            actor_scope_digest=actor_scope_digest(project),
            basis_digest=digest,
            impact=impact.plan,
            diff=changes,
            summary_groups=semantic_groups(changes),
        )
        return RestorePlan(basis, preview, target, snapshot)

    def read_revision(self, project: str, digest: str) -> tuple[SemanticRevision, EntitySnapshot]:
        try:
            self.access.require_revision(project, digest)
        except ResourceScopeError as exc:
            raise RestoreError("RESTORE_ACCESS_DENIED") from exc
        revision = self.ledger.read_revision_by_digest(project, digest)
        snapshot = None if revision is None else self.ledger.read_snapshot(revision.snapshot_id)
        if revision is None or snapshot is None:
            raise RestoreError("RESTORE_TARGET_MISMATCH")
        return revision, snapshot

    def _sources(
        self, project: str, refs: tuple[str, ...], frozen: dict[str, tuple[str, str | None]]
    ) -> dict[str, object]:
        bindings = self.governance.list_source_bindings(project)
        result: dict[str, object] = {}
        for ref in refs:
            self.access.require_read(project, ref)
            span = self.artifacts.read_evidence(ref)
            if span is None or span.project_id != project:
                raise RestoreError("RESTORE_SOURCE_UNAVAILABLE")
            if frozen.get(ref) != (span.source_version_id, span.text_sha256):
                raise RestoreError("RESTORE_SOURCE_DRIFT")
            active = tuple(
                binding
                for binding in bindings
                if binding.artifact_id == span.artifact_id and binding.state == "ACTIVE"
            )
            if not active:
                raise RestoreError("RESTORE_SOURCE_UNAVAILABLE")
            artifact = self.artifacts.read_artifact(span.artifact_id)
            versions = self.artifacts.list_source_versions(project, span.artifact_id)
            if artifact is None or span.source_version_id not in versions:
                raise RestoreError("RESTORE_SOURCE_UNAVAILABLE")
            if len(versions) != 1 or str(span.cutoff_state) in {
                "AFTER_CUTOFF",
                "PROHIBITED_CONTEXT",
            }:
                raise RestoreError("RESTORE_SOURCE_DRIFT")
            result[ref] = {
                "span": span.model_dump(mode="json"),
                "artifact": artifact.model_dump(mode="json"),
                "bindings": [binding.model_dump(mode="json") for binding in active],
            }
        return result

    def _scope_basis(
        self, project: str, digests: tuple[str, ...], sources: tuple[str, ...]
    ) -> dict[str, str]:
        frontier = [*(f"revision:{digest}" for digest in digests), *sources]
        checked: dict[str, str] = {}
        while frontier:
            ref = frontier.pop()
            if ref in checked:
                continue
            record = self.scopes.read(project, ref)
            if record is not None:
                checked[ref] = record.record_digest
                frontier.extend(record.parent_refs)
            else:
                checked[ref] = "UNSCOPED"
        return checked

    def _active_consumers(self, project: str, object_id: str) -> dict[str, object]:
        threads = [
            thread
            for thread in self.threads.list(project)
            if object_id in thread.current_object_ids
        ]
        if any(str(thread.execution_state) in {"RUNNING", "PAUSE_PENDING"} for thread in threads):
            raise RestoreError("RESTORE_ACTIVE_CONSUMER")
        executions = [
            item for item in self.executions.list_executions(project) if item.object_id == object_id
        ]
        if any(
            item.state in {"RUNNING", "UNKNOWN_COMPLETION", "CANCEL_REQUESTED"}
            for item in executions
        ):
            raise RestoreError("RESTORE_ACTIVE_CONSUMER")
        execution_attempts = [
            attempt
            for execution in executions
            for attempt in self.executions.list_attempts(project, execution.plan_execution_id)
        ]
        if any(
            attempt.state in {"RUNNING", "DISPATCHED", "UNKNOWN_COMPLETION", "CANCEL_REQUESTED"}
            or attempt.effect_state in {"POSSIBLE", "UNKNOWN", "EFFECT_MAY_CONTINUE"}
            for attempt in execution_attempts
        ):
            raise RestoreError("RESTORE_ACTIVE_CONSUMER")
        thread_ids = {thread.thread_id for thread in threads}
        attempts = [
            record
            for record in self.controls.list(project, "RESEARCH_EXECUTION", "ResearchAttempt")
            if isinstance(record.payload.get("continuation"), dict)
            and cast(dict[str, object], record.payload["continuation"]).get("thread_id")
            in thread_ids
        ]
        if any(
            record.payload.get("external_effect_state") not in {None, "NONE", "RETURNED"}
            for record in attempts
        ):
            raise RestoreError("RESTORE_ACTIVE_CONSUMER")
        return {
            "threads": {
                thread.thread_id: {
                    "revision": thread.revision,
                    "execution_state": str(thread.execution_state),
                }
                for thread in threads
            },
            "executions": {item.plan_execution_id: item.revision_digest for item in executions},
            "execution_attempts": {
                item.attempt_id: item.revision_digest for item in execution_attempts
            },
            "attempts": {record.record_id: record.record_digest for record in attempts},
        }
