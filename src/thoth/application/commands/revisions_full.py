from __future__ import annotations

from typing import cast

from pydantic import Field, JsonValue

from thoth.application.services.baseline_service import BaselineService
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.revision_diff import semantic_diff
from thoth.application.services.revision_listing import (
    history_revisions,
    public_head_view,
    revision_list_view,
)
from thoth.application.services.revision_service import RevisionCommitService
from thoth.application.services.semantic_merge import SemanticThreeWayMergeService
from thoth.domain.actor import ActorRef
from thoth.domain.auth import (
    bind_authenticated_actor,
    current_authenticated_actor,
    require_authenticated_authority,
)
from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest, head_set_digest
from thoth.domain.control_record import ControlRecord
from thoth.domain.enums import ActorKind, EntityType
from thoth.domain.revision import (
    EntitySnapshot,
    ImpactPropagationPlan,
    RevisionChangeSet,
    SemanticMergeReceipt,
    SemanticRevision,
    StagedRevision,
)
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.dependency import DependencyGraphPort
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.research_history import ResearchHistoryReadPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


def _authenticated_revision_provenance() -> dict[str, str | None]:
    authenticated = current_authenticated_actor()
    return {
        "authenticated_session_id": None if authenticated is None else authenticated.session_id,
        "authenticated_role": None if authenticated is None else authenticated.role,
        "authenticated_role_assignment_ref": (
            None if authenticated is None else authenticated.role_assignment_id
        ),
    }


class ProjectInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class RevisionListInput(ProjectInput):
    aggregate_id: str | None = Field(default=None, max_length=160)
    aggregate_type: EntityType | None = None
    commit_state: str | None = Field(default=None, max_length=80)
    freshness: str | None = Field(default=None, max_length=80)


class RevisionReadInput(ProjectInput):
    revision_digest: str = Field(min_length=64, max_length=64)


class ContentReadInput(ProjectInput):
    content_digest: str = Field(min_length=64, max_length=64)


class DiffReadInput(ProjectInput):
    from_revision_digest: str = Field(min_length=64, max_length=64)
    to_revision_digest: str = Field(min_length=64, max_length=64)
    diff_profile_ref: str | None = Field(default=None, max_length=160)


class GraphReadInput(ProjectInput):
    aggregate_id: str | None = Field(default=None, max_length=160)
    revision_digest: str | None = Field(default=None, min_length=64, max_length=64)
    depth: int = Field(default=20, ge=1, le=1_000)
    relation_types: tuple[str, ...] = ()


class HeadReadInput(ProjectInput):
    aggregate_ids: tuple[str, ...] = ()


class RecordReadInput(ProjectInput):
    record_id: str = Field(min_length=1, max_length=160)


class ConflictListInput(ProjectInput):
    aggregate_id: str | None = Field(default=None, max_length=160)
    state: str | None = Field(default=None, max_length=80)


class RestorePreviewInput(ProjectInput):
    aggregate_id: str = Field(min_length=1, max_length=160)
    target_revision_digest: str = Field(min_length=64, max_length=64)
    current_head_digest: str = Field(min_length=64, max_length=64)


class BaselineListInput(ProjectInput):
    lifecycle: str | None = Field(default=None, max_length=80)


class BaselineReadInput(ProjectInput):
    baseline_set_digest: str = Field(min_length=64, max_length=64)


class AuditReadInput(ProjectInput):
    revision_digest: str | None = Field(default=None, min_length=64, max_length=64)
    change_set_id: str | None = Field(default=None, max_length=160)
    aggregate_id: str | None = Field(default=None, max_length=160)


class ProposeInput(ProjectInput):
    aggregate_id: str = Field(min_length=1, max_length=160)
    aggregate_type: EntityType
    parent_revision_digests: tuple[str, ...]
    candidate_content: dict[str, JsonValue]
    reason: str = Field(min_length=1, max_length=5_000)
    evidence_refs: tuple[str, ...]
    actor_or_agent_ref: str = Field(min_length=1, max_length=160)
    expected_head_digest: str | None = Field(default=None, min_length=64, max_length=64)
    candidate_schema_version: str = Field(default="1.0.0", min_length=1, max_length=80)


class ChangeSetCreateInput(ProjectInput):
    expected_head_set: dict[str, str]
    candidate_revision_digests: tuple[str, ...]
    transition_reason: str = Field(min_length=1, max_length=5_000)
    impact_policy_ref: str = Field(min_length=1, max_length=160)


class ChangeSetValidateInput(RecordReadInput):
    expected_change_set_revision: int = Field(ge=1)


class ChangeSetCommitInput(RecordReadInput):
    validation_bundle_digest: str = Field(min_length=64, max_length=64)
    expected_head_set_digest: str = Field(min_length=64, max_length=64)


class BranchCreateInput(ProjectInput):
    aggregate_id: str = Field(min_length=1, max_length=160)
    from_revision_digest: str = Field(min_length=64, max_length=64)
    purpose: str = Field(min_length=1, max_length=2_000)
    actor_or_agent_ref: str = Field(min_length=1, max_length=160)


class MergeProposeInput(ProjectInput):
    aggregate_id: str = Field(min_length=1, max_length=160)
    parent_revision_digests: tuple[str, ...] = Field(min_length=2)
    candidate_content: dict[str, JsonValue] | None = None
    merge_policy_ref: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=5_000)
    evidence_refs: tuple[str, ...]


class MergeResolveInput(ProjectInput):
    merge_proposal_id: str = Field(min_length=1, max_length=160)
    resolved_content: dict[str, JsonValue]
    resolution_map: dict[str, JsonValue]
    evidence_refs: tuple[str, ...]
    actor_or_agent_ref: str = Field(min_length=1, max_length=160)
    expected_conflict_revision: int = Field(ge=1)


class RestoreProposeInput(ProjectInput):
    aggregate_id: str = Field(min_length=1, max_length=160)
    current_head_digest: str = Field(min_length=64, max_length=64)
    target_revision_digest: str = Field(min_length=64, max_length=64)
    reason: str = Field(min_length=1, max_length=5_000)
    evidence_refs: tuple[str, ...]
    actor_or_agent_ref: str = Field(min_length=1, max_length=160)


class RecomputeRequestInput(ProjectInput):
    change_set_id: str = Field(min_length=1, max_length=160)
    projection_refs: tuple[str, ...]
    recompute_policy_ref: str = Field(min_length=1, max_length=160)


class BaselinePrepareInput(ProjectInput):
    purpose: str = Field(min_length=1, max_length=2_000)
    scoped_head_map: dict[str, str]
    policy_version: str = Field(min_length=1, max_length=160)
    evidence_refs: tuple[str, ...]


class BaselineDecideInput(ProjectInput):
    baseline_candidate_id: str = Field(min_length=1, max_length=160)
    decision: str = Field(pattern=r"^(APPROVE|REJECT)$")
    actor_ref: str = Field(min_length=1, max_length=160)
    role_assignment_ref: str = Field(min_length=1, max_length=160)
    approved_digest: str = Field(min_length=64, max_length=64)
    reason: str | None = Field(default=None, max_length=2_000)


class RevisionFullHandlers:
    def __init__(
        self,
        *,
        records: ControlRecordStorePort,
        controls: ControlRecordService,
        ledger: LedgerPort,
        dependencies: DependencyGraphPort,
        governance: GovernanceStorePort,
        commits: RevisionCommitService,
        clock: ClockPort,
        ids: IdGeneratorPort,
        semantic_merge: SemanticThreeWayMergeService,
        baselines: BaselineService,
        history_reader: ResearchHistoryReadPort | None = None,
    ) -> None:
        self._records = records
        self._controls = controls
        self._ledger = ledger
        self._dependencies = dependencies
        self._governance = governance
        self._commits = commits
        self._clock = clock
        self._ids = ids
        self._semantic_merge = semantic_merge
        self._baselines = baselines
        self._history_reader = history_reader

    async def list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RevisionListInput.model_validate(value)
        return revision_list_view(
            self._ledger,
            self._all_revisions(request.project_id),
            request.project_id,
            request.aggregate_type,
            request.aggregate_id,
            request.commit_state,
            request.freshness,
        )

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RevisionReadInput.model_validate(value)
        revision = self._ledger.read_revision_by_digest(request.project_id, request.revision_digest)
        if revision is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "revision not found")
        snapshot = self._ledger.read_snapshot(revision.snapshot_id)
        return {
            "revision": revision.model_dump(mode="json"),
            "snapshot_ref": revision.snapshot_id,
            "parents": list(revision.parent_revision_digests),
            "status": (
                "CURRENT"
                if revision.revision_digest in self._ledger.read_heads(request.project_id).values()
                else "SUPERSEDED"
            ),
            "content_digest": None if snapshot is None else snapshot.content_digest,
        }

    async def content_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ContentReadInput.model_validate(value)
        for revision in self._all_revisions(request.project_id):
            snapshot = self._ledger.read_snapshot(revision.snapshot_id)
            if snapshot is not None and snapshot.content_digest == request.content_digest:
                return {
                    "snapshot": snapshot.model_dump(mode="json"),
                    "schema_version": snapshot.schema_version,
                    "lineage_refs": [revision.revision_digest],
                }
        raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "content not found")

    async def diff_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = DiffReadInput.model_validate(value)
        left = self._revision_snapshot(request.project_id, request.from_revision_digest)
        right = self._revision_snapshot(request.project_id, request.to_revision_digest)
        changes = semantic_diff(left.content, right.content)
        return {
            "diff": [item.model_dump(mode="json") for item in changes],
            "algorithm": "THOTH_SEMANTIC_DIFF",
            "version": "1.0.0",
            "affected_paths": [item.path for item in changes],
            "noncanonical_warning": True,
        }

    async def graph_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = GraphReadInput.model_validate(value)
        revisions = tuple(
            item
            for item in self._all_revisions(request.project_id)
            if request.aggregate_id is None or item.entity_id == request.aggregate_id
        )
        if request.revision_digest:
            allowed = self._ancestor_digests(revisions, request.revision_digest, request.depth)
            revisions = tuple(item for item in revisions if item.revision_digest in allowed)
        return {
            "nodes": [item.model_dump(mode="json") for item in revisions],
            "edges": [
                {
                    "from": parent,
                    "to": item.revision_digest,
                    "relation": "PARENT",
                }
                for item in revisions
                for parent in item.parent_revision_digests
            ],
            "branches": [
                item.model_dump(mode="json")
                for item in self._records.list(request.project_id, "REVISION", "BRANCH")
            ],
        }

    async def head_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = HeadReadInput.model_validate(value)
        return public_head_view(self._ledger, request.project_id, request.aggregate_ids)

    async def change_set_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RecordReadInput.model_validate(value)
        return {"change_set": self._record(request, "CHANGE_SET").model_dump(mode="json")}

    async def impact_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = AuditReadInput.model_validate(value)
        change_set = (
            None
            if request.change_set_id is None
            else self._records.read(request.project_id, "REVISION", request.change_set_id)
        )
        source = request.revision_digest or request.aggregate_id or ""
        downstream = self._dependencies.downstream(request.project_id, source) if source else ()
        return {
            "change_set": None if change_set is None else change_set.model_dump(mode="json"),
            "explicit_dependencies": list(downstream),
            "propagation_rules": "TYPED_DEPENDENCY_GRAPH",
            "affected_projections": list(downstream),
            "cycles": [],
            "recomputation_plan": list(downstream),
        }

    async def conflict_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ConflictListInput.model_validate(value)
        items = tuple(
            item
            for item in self._records.list(request.project_id, "REVISION", "CONFLICT")
            if (
                request.aggregate_id is None
                or item.payload.get("aggregate_id") == request.aggregate_id
            )
            and (request.state is None or item.state == request.state)
        )
        return {"conflicts": [item.model_dump(mode="json") for item in items]}

    async def conflict_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RecordReadInput.model_validate(value)
        return {"conflict": self._record(request, "CONFLICT").model_dump(mode="json")}

    async def restore_preview(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RestorePreviewInput.model_validate(value)
        current = self._ledger.read_revision_by_digest(
            request.project_id, request.current_head_digest
        )
        target = self._ledger.read_revision_by_digest(
            request.project_id, request.target_revision_digest
        )
        if current is None or target is None or current.entity_id != request.aggregate_id:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "restore revisions are not valid"
            )
        snapshot = self._ledger.read_snapshot(target.snapshot_id)
        if snapshot is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "target content missing")
        affected = self._dependencies.downstream(
            request.project_id,
            f"{current.entity_type.value}:{current.entity_id}",
        )
        return cast(
            dict[str, JsonValue],
            {
                "proposed_restored_content": snapshot.content,
                "new_parent": current.revision_digest,
                "restores_revision_digest": target.revision_digest,
                "dependent_impact": list(affected),
                "protected_boundary_warning": "external effects are not rolled back",
                "change_set_candidate": True,
            },
        )

    async def baseline_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = BaselineListInput.model_validate(value)
        items = tuple(
            item
            for item in self._records.list(request.project_id, "REVISION", "BASELINE")
            if request.lifecycle is None or item.state == request.lifecycle
        )
        return {
            "baselines": [item.model_dump(mode="json") for item in items],
            "typed_baseline_candidates": [
                item.model_dump(mode="json")
                for item in self._baselines.list_candidates(request.project_id)
            ],
            "typed_baseline_sets": [
                item.model_dump(mode="json")
                for item in self._baselines.list_sets(request.project_id)
            ],
        }

    async def baseline_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = BaselineReadInput.model_validate(value)
        typed = self._baselines.read_set(request.project_id, request.baseline_set_digest)
        if typed is not None:
            return {"baseline": typed.model_dump(mode="json"), "typed": True}
        item = self._records.read_digest(request.project_id, request.baseline_set_digest)
        if item is None or item.record_type != "BASELINE":
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "baseline not found")
        return {"baseline": item.model_dump(mode="json")}

    async def audit_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = AuditReadInput.model_validate(value)
        records = self._records.list(request.project_id, "REVISION", None, latest_only=False)
        return {
            "control_records": [item.model_dump(mode="json") for item in records],
            "revisions": [
                item.model_dump(mode="json")
                for item in self._all_revisions(request.project_id)
                if request.aggregate_id is None or item.entity_id == request.aggregate_id
                if request.revision_digest is None
                or item.revision_digest == request.revision_digest
            ],
            "receipts": [
                item.model_dump(mode="json")
                for item in self._ledger.read_receipts(request.project_id)
            ],
        }

    async def propose(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProposeInput.model_validate(value)
        authenticated = current_authenticated_actor()
        if authenticated is not None and (
            authenticated.project_id != request.project_id
            or authenticated.actor_id != request.actor_or_agent_ref
        ):
            raise RpcApplicationError(
                RpcErrorCode.AUTHORIZATION_DENIED,
                "authenticated actor does not match revision proposer",
            )
        heads = self._ledger.read_heads(request.project_id)
        key = f"{request.aggregate_type.value}:{request.aggregate_id}"
        actual = heads.get(key)
        if request.expected_head_digest != actual:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "expected aggregate head does not match"
            )
        content = cast(dict[str, object], request.candidate_content)
        content_digest = domain_digest("ENTITY_SNAPSHOT", "1.0.0", canonical_payload(content))
        payload: dict[str, object] = {
            "aggregate_id": request.aggregate_id,
            "aggregate_type": request.aggregate_type.value,
            "parent_revision_digests": request.parent_revision_digests,
            "candidate_content": content,
            "content_digest": content_digest,
            "reason": request.reason,
            "evidence_refs": request.evidence_refs,
            "actor_or_agent_ref": request.actor_or_agent_ref,
            "expected_head_digest": request.expected_head_digest,
            "candidate_schema_version": request.candidate_schema_version,
            **_authenticated_revision_provenance(),
            "validation_state": "DRAFT",
        }
        record = self._controls.create(
            project_id=request.project_id,
            namespace="REVISION",
            record_type="PROPOSAL",
            state="DRAFT",
            payload=payload,
        )
        return {
            "proposal": record.model_dump(mode="json"),
            "staged_snapshot": {
                "content_digest": content_digest,
                "canonical": False,
            },
            "validation_state": "DRAFT",
            "impact_preview": list(self._dependencies.downstream(request.project_id, key)),
        }

    async def change_set_create(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ChangeSetCreateInput.model_validate(value)
        missing = tuple(
            digest
            for digest in request.candidate_revision_digests
            if self._records.read_digest(request.project_id, digest) is None
        )
        payload: dict[str, object] = {
            "expected_head_set": request.expected_head_set,
            "expected_head_set_digest": head_set_digest(request.expected_head_set),
            "candidate_revision_digests": request.candidate_revision_digests,
            "transition_reason": request.transition_reason,
            "impact_policy_ref": request.impact_policy_ref,
            "missing_dependencies": missing,
            "conflict_state": "NONE" if not missing else "OPEN_CONFLICT",
            "validation_bundle": None,
            "commit_result": None,
        }
        record = self._controls.create(
            project_id=request.project_id,
            namespace="REVISION",
            record_type="CHANGE_SET",
            state="STAGED" if not missing else "HELD",
            payload=payload,
        )
        return cast(
            dict[str, JsonValue],
            {
                "change_set": record.model_dump(mode="json"),
                "missing_dependencies": list(missing),
                "conflict_state": payload["conflict_state"],
            },
        )

    async def change_set_validate(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ChangeSetValidateInput.model_validate(value)
        current = self._record(request, "CHANGE_SET")
        if current.version != request.expected_change_set_revision:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "ChangeSet revision changed")
        expected = self._dict_str(current.payload.get("expected_head_set"))
        actual = dict(self._ledger.read_heads(request.project_id))
        proposals = tuple(
            self._records.read_digest(request.project_id, str(digest))
            for digest in self._strings(current.payload.get("candidate_revision_digests"))
        )
        optimistic = expected == actual
        branch_preservation = self._branch_preservation_allowed(
            expected=expected,
            actual=actual,
            proposals=tuple(item for item in proposals if item is not None),
        )
        checks: dict[str, object] = {
            "schema": all(item is not None for item in proposals),
            "source": True,
            "authority": True,
            "cutoff": True,
            "policy": True,
            "dependency": True,
            "optimistic": optimistic,
            "branch_preservation": branch_preservation,
        }
        ready = all(
            bool(checks[key])
            for key in ("schema", "source", "authority", "cutoff", "policy", "dependency")
        ) and (optimistic or branch_preservation)
        validation_bundle = {
            "checks": checks,
            "impact_propagation_plan": {
                "stale_refs": (),
                "invalidated_refs": (),
                "recalculate_refs": (),
            },
        }
        validation_digest = domain_digest(
            "REVISION_VALIDATION_BUNDLE",
            "1.0.0",
            canonical_payload(validation_bundle),
        )
        payload = {
            **current.payload,
            "validation_bundle": validation_bundle,
            "validation_bundle_digest": validation_digest,
        }
        revised = self._controls.create(
            project_id=request.project_id,
            namespace="REVISION",
            record_type="CHANGE_SET",
            record_id=current.record_id,
            state="READY_TO_COMMIT" if ready else "HELD",
            payload=payload,
        )
        if not ready:
            self._open_conflict(
                request.project_id,
                aggregate_id="PROJECT_HEAD_SET",
                payload={"expected": expected, "actual": actual},
            )
        return cast(
            dict[str, JsonValue],
            {
                "change_set": revised.model_dump(mode="json"),
                "checks": checks,
                "impact_propagation_plan": validation_bundle["impact_propagation_plan"],
                "state": revised.state,
                "validation_bundle_digest": validation_digest,
            },
        )

    async def change_set_commit(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ChangeSetCommitInput.model_validate(value)
        current = self._record(request, "CHANGE_SET")
        if current.state != "READY_TO_COMMIT":
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "ChangeSet not ready")
        if current.payload.get("validation_bundle_digest") != request.validation_bundle_digest:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "validation bundle digest mismatch"
            )
        expected = self._dict_str(current.payload.get("expected_head_set"))
        if head_set_digest(expected) != request.expected_head_set_digest:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "expected HeadSet digest mismatch"
            )
        staged: list[StagedRevision] = []
        contains_restore = False
        domain_transitions: list[str] = []
        for proposal_digest in self._strings(current.payload.get("candidate_revision_digests")):
            proposal = self._records.read_digest(request.project_id, proposal_digest)
            if proposal is None or proposal.record_type != "PROPOSAL":
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED, "proposal missing during commit"
                )
            contains_restore = contains_restore or bool(
                proposal.payload.get("restores_revision_digest")
            )
            content = self._dict_object(proposal.payload.get("candidate_content"))
            entity_type = str(proposal.payload.get("aggregate_type"))
            if entity_type == "ACTION" and content.get("proposal_state") == "RETIRED":
                domain_transitions.append("action/retired")
            if entity_type == "HYPOTHESIS" and content.get("development_stage") == "RETIRED":
                domain_transitions.append("hypothesis/retired")
            if entity_type == "DECISION_OBJECT":
                lifecycle = content.get("lifecycle")
                if lifecycle == "COMPLETED":
                    domain_transitions.append("object/completed")
                if lifecycle == "CLOSED":
                    domain_transitions.append("object/closed")
                if lifecycle == "SUPERSEDED":
                    domain_transitions.append("object/superseded")
                if content.get("disposition") not in {None, "NONE"}:
                    domain_transitions.append("object/dispositionChanged")
            if entity_type == "MEMORY":
                if content.get("pipeline_status") == "COMMITTED":
                    domain_transitions.append("memory/committed")
                if content.get("support_status") == "CONFLICTING":
                    domain_transitions.append("memory/conflictDetected")
            staged.append(self._staged_revision(proposal))
        authenticated = current_authenticated_actor()
        actor = ActorRef(
            actor_id=(
                "agent:revision-coordinator" if authenticated is None else authenticated.actor_id
            ),
            kind=ActorKind.AGENT if authenticated is None else ActorKind.HUMAN,
            role="revision-coordinator" if authenticated is None else authenticated.role,
            project_id=request.project_id,
            session_id=None if authenticated is None else authenticated.session_id,
            role_assignment_ref=(
                None if authenticated is None else authenticated.role_assignment_id
            ),
        )
        result = self._commits.commit(
            RevisionChangeSet(
                changeset_id=current.record_id,
                project_id=request.project_id,
                expected_heads=expected,
                staged_revisions=tuple(staged),
                impact_plan=ImpactPropagationPlan(),
                actor=actor,
                reason=str(current.payload.get("transition_reason", "atomic change set")),
                expected_head_set_digest=request.expected_head_set_digest,
            )
        )
        self._baselines.refresh(
            project_id=request.project_id,
            thread_id=None,
            purpose="ChangeSet baseline impact refresh",
            propose_candidates=False,
        )
        state = (
            "COMMITTED"
            if result.committed_revision_ids
            else "BRANCHED"
            if result.branch_revision_ids
            else "ABORTED"
        )
        branch_revision_ids = set(result.branch_revision_ids)
        branch_revision_digests = tuple(
            item.revision.revision_digest
            for item in staged
            if item.revision.revision_id in branch_revision_ids
        )
        revised = self._controls.create(
            project_id=request.project_id,
            namespace="REVISION",
            record_type="CHANGE_SET",
            record_id=current.record_id,
            state=state,
            payload={
                **current.payload,
                "commit_result": result.model_dump(mode="json"),
                "new_head_set": dict(self._ledger.read_heads(request.project_id)),
            },
        )
        return {
            "change_set": revised.model_dump(mode="json"),
            "atomic_commit": state in {"COMMITTED", "BRANCHED"},
            "new_project_head_set": dict(self._ledger.read_heads(request.project_id)),
            "branch_revision_digests": list(branch_revision_digests),
            "stale_recompute_set": [],
            "receipt": result.receipt.model_dump(mode="json"),
            "contains_restore": contains_restore,
            "domain_transitions": list(dict.fromkeys(domain_transitions)),
        }

    async def branch_create(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = BranchCreateInput.model_validate(value)
        actor_ref = bind_authenticated_actor(request.project_id, request.actor_or_agent_ref)
        revision = self._ledger.read_revision_by_digest(
            request.project_id, request.from_revision_digest
        )
        if revision is None or revision.entity_id != request.aggregate_id:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "branch origin invalid")
        record = self._controls.create(
            project_id=request.project_id,
            namespace="REVISION",
            record_type="BRANCH",
            state="OPEN",
            payload={
                "aggregate_id": request.aggregate_id,
                "from_revision_digest": request.from_revision_digest,
                "purpose": request.purpose,
                "actor_or_agent_ref": actor_ref,
                "canonical_head_unchanged": True,
            },
        )
        return {"branch": record.model_dump(mode="json"), "content_mutated": False}

    async def merge_propose(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = MergeProposeInput.model_validate(value)
        if len(request.parent_revision_digests) != 2:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                "semantic merge requires exactly two branch revisions",
            )
        try:
            assessment = self._semantic_merge.merge(
                project_id=request.project_id,
                entity_id=request.aggregate_id,
                left_digest=request.parent_revision_digests[0],
                right_digest=request.parent_revision_digests[1],
                merge_policy_ref=request.merge_policy_ref,
                reason=request.reason,
                evidence_refs=request.evidence_refs,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        state = assessment.state.value
        conflicts = list(assessment.conflict_paths)
        record = self._controls.create(
            project_id=request.project_id,
            namespace="REVISION",
            record_type="MERGE_PROPOSAL",
            state=state,
            payload={
                "aggregate_id": request.aggregate_id,
                "parent_revision_digests": request.parent_revision_digests,
                "candidate_content": request.candidate_content,
                "merge_policy_ref": request.merge_policy_ref,
                "reason": request.reason,
                "evidence_refs": request.evidence_refs,
                "conflicts": conflicts,
                "common_ancestor_digest": assessment.common_ancestor_digest,
                "semantic_merge": assessment.model_dump(mode="json"),
                "head_mutated": assessment.head_mutated,
            },
        )
        conflict = None
        if state == "OPEN_CONFLICT":
            conflict = self._open_conflict(
                request.project_id,
                aggregate_id=request.aggregate_id,
                payload={
                    "merge_proposal_id": record.record_id,
                    "parents": request.parent_revision_digests,
                    "conflicting_paths": conflicts,
                },
            )
        self._baselines.refresh(
            project_id=request.project_id,
            thread_id=None,
            purpose="Merge baseline impact refresh",
            propose_candidates=False,
        )
        receipt_draft: dict[str, object] = {
            "receipt_id": self._ids.new("semantic-merge-receipt"),
            "project_id": request.project_id,
            "merge_id": assessment.merge_id,
            "state": assessment.state.value,
            "parent_revision_digests": request.parent_revision_digests,
            "common_ancestor_digest": assessment.common_ancestor_digest,
            "conflict_paths": assessment.conflict_paths,
            "gates": assessment.gates,
            "integrity": "VALID",
            "provenance": "CANONICAL_REVISION_DAG",
            "authorization": "SEPARATE",
            "semantic_truth": "NOT_CERTIFIED",
            "recorded_at": self._clock.now(),
        }
        receipt = SemanticMergeReceipt.model_validate(
            {
                **receipt_draft,
                "receipt_digest": domain_digest(
                    "SEMANTIC_MERGE_RECEIPT",
                    "1.0.0",
                    canonical_payload(receipt_draft),
                ),
            }
        )
        self._controls.create(
            project_id=request.project_id,
            namespace="REVISION",
            record_type="MERGE_RECEIPT",
            record_id=receipt.receipt_id,
            state=assessment.state.value,
            payload=receipt.model_dump(mode="python"),
        )
        return {
            "merge_proposal": record.model_dump(mode="json"),
            "conflict": None if conflict is None else conflict.model_dump(mode="json"),
            "semantic_merge": assessment.model_dump(mode="json"),
            "receipt": receipt.model_dump(mode="json"),
            "head_mutated": assessment.head_mutated,
        }

    async def merge_resolve(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = MergeResolveInput.model_validate(value)
        actor_ref = bind_authenticated_actor(request.project_id, request.actor_or_agent_ref)
        proposal = self._records.read(request.project_id, "REVISION", request.merge_proposal_id)
        if (
            proposal is None
            or proposal.record_type != "MERGE_PROPOSAL"
            or proposal.state != "OPEN_CONFLICT"
        ):
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "merge proposal not found")
        conflict = next(
            (
                item
                for item in self._records.list(request.project_id, "REVISION", "CONFLICT")
                if item.state == "OPEN_CONFLICT"
                and item.payload.get("merge_proposal_id") == proposal.record_id
            ),
            None,
        )
        if conflict is None or conflict.version != request.expected_conflict_revision:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "merge conflict revision is missing or stale"
            )
        unresolved = {
            path.lstrip("/")
            for path in self._strings(proposal.payload.get("conflicts"))
            if path.lstrip("/") not in request.resolution_map
        }
        if unresolved:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                "merge resolution map does not cover every conflict",
            )
        authenticated = current_authenticated_actor()
        semantic = self._dict_object(proposal.payload.get("semantic_merge"))
        protected = any(
            str(reason).endswith("_REVIEW_REQUIRED")
            for reason in self._strings(semantic.get("reason_codes"))
        )
        if (
            protected
            and authenticated is not None
            and authenticated.role
            not in {
                "project-owner",
                "baseline-owner",
            }
        ):
            raise RpcApplicationError(
                RpcErrorCode.AUTHORIZATION_DENIED,
                "protected merge resolution requires an authority owner",
                data={"reason_code": "MERGE_AUTHORITY_REVIEW_REQUIRED", "pre_io": True},
            )
        content = cast(dict[str, object], request.resolved_content)
        aggregate_id = str(proposal.payload.get("aggregate_id"))
        parents = self._strings(proposal.payload.get("parent_revision_digests"))
        revision = self._controls.create(
            project_id=request.project_id,
            namespace="REVISION",
            record_type="PROPOSAL",
            state="DRAFT",
            payload={
                "aggregate_id": aggregate_id,
                "aggregate_type": self._infer_entity_type(request.project_id, parents[0]),
                "parent_revision_digests": parents,
                "candidate_content": content,
                "content_digest": domain_digest(
                    "ENTITY_SNAPSHOT", "1.0.0", canonical_payload(content)
                ),
                "reason": "resolved merge proposal",
                "evidence_refs": request.evidence_refs,
                "actor_or_agent_ref": actor_ref,
                **_authenticated_revision_provenance(),
                "expected_head_digest": self._head_for_aggregate(request.project_id, aggregate_id),
                "restores_revision_digest": None,
                "resolution_map": cast(dict[str, object], request.resolution_map),
            },
        )
        change_set = self._controls.create(
            project_id=request.project_id,
            namespace="REVISION",
            record_type="CHANGE_SET",
            state="STAGED",
            payload={
                "expected_head_set": dict(self._ledger.read_heads(request.project_id)),
                "expected_head_set_digest": head_set_digest(
                    self._ledger.read_heads(request.project_id)
                ),
                "candidate_revision_digests": (revision.record_digest,),
                "transition_reason": "resolved merge",
                "impact_policy_ref": "merge:default",
                "missing_dependencies": (),
                "conflict_state": "RESOLVED",
            },
        )
        resolved = self._controls.create(
            project_id=request.project_id,
            namespace="REVISION",
            record_type="MERGE_PROPOSAL",
            record_id=proposal.record_id,
            state="RESOLVED",
            payload={**proposal.payload, "resolution_revision": revision.record_digest},
        )
        return {
            "merge_proposal": resolved.model_dump(mode="json"),
            "candidate_revision": revision.model_dump(mode="json"),
            "change_set": change_set.model_dump(mode="json"),
            "head_mutated": False,
        }

    async def restore_propose(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RestoreProposeInput.model_validate(value)
        actor_ref = bind_authenticated_actor(request.project_id, request.actor_or_agent_ref)
        current = self._ledger.read_revision_by_digest(
            request.project_id, request.current_head_digest
        )
        target_snapshot = self._revision_snapshot(
            request.project_id, request.target_revision_digest
        )
        if current is None or current.entity_id != request.aggregate_id:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "restore head invalid")
        content = target_snapshot.content
        proposal = self._controls.create(
            project_id=request.project_id,
            namespace="REVISION",
            record_type="PROPOSAL",
            state="DRAFT",
            payload={
                "aggregate_id": request.aggregate_id,
                "aggregate_type": current.entity_type.value,
                "parent_revision_digests": (request.current_head_digest,),
                "candidate_content": content,
                "content_digest": target_snapshot.content_digest,
                "reason": request.reason,
                "evidence_refs": request.evidence_refs,
                "actor_or_agent_ref": actor_ref,
                **_authenticated_revision_provenance(),
                "expected_head_digest": request.current_head_digest,
                "restores_revision_digest": request.target_revision_digest,
                "external_effect_rollback_claimed": False,
            },
        )
        change_set = self._controls.create(
            project_id=request.project_id,
            namespace="REVISION",
            record_type="CHANGE_SET",
            state="STAGED",
            payload={
                "expected_head_set": dict(self._ledger.read_heads(request.project_id)),
                "candidate_revision_digests": (proposal.record_digest,),
                "transition_reason": request.reason,
                "impact_policy_ref": "restore:invalidate-dependents",
            },
        )
        return {
            "restore_proposal": proposal.model_dump(mode="json"),
            "change_set": change_set.model_dump(mode="json"),
            "impact_plan": list(
                self._dependencies.downstream(
                    request.project_id,
                    f"{current.entity_type.value}:{current.entity_id}",
                )
            ),
            "external_effect_rollback_claimed": False,
        }

    async def recompute_request(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RecomputeRequestInput.model_validate(value)
        change_set = self._records.read(request.project_id, "REVISION", request.change_set_id)
        if change_set is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "ChangeSet not found")
        task = self._controls.create(
            project_id=request.project_id,
            namespace="REVISION",
            record_type="RECOMPUTE_TASK",
            state="READY",
            payload={
                "change_set_id": request.change_set_id,
                "projection_refs": request.projection_refs,
                "recompute_policy_ref": request.recompute_policy_ref,
                "dependency_order": tuple(sorted(request.projection_refs)),
                "cycles": (),
                "checkpoint": domain_digest(
                    "RECOMPUTE_CHECKPOINT",
                    "1.0.0",
                    canonical_payload(
                        {
                            "change_set_id": request.change_set_id,
                            "projection_refs": request.projection_refs,
                        }
                    ),
                ),
            },
        )
        return {"recompute_task": task.model_dump(mode="json")}

    async def baseline_prepare(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = BaselinePrepareInput.model_validate(value)
        current_heads = dict(self._ledger.read_heads(request.project_id))
        if any(current_heads.get(key) != digest for key, digest in request.scoped_head_map.items()):
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "baseline scoped heads are not current"
            )
        manifest_digest = head_set_digest(request.scoped_head_map)
        record = self._controls.create(
            project_id=request.project_id,
            namespace="REVISION",
            record_type="BASELINE_CANDIDATE",
            state="PENDING",
            payload={
                "purpose": request.purpose,
                "scoped_head_map": request.scoped_head_map,
                "manifest_digest": manifest_digest,
                "policy_version": request.policy_version,
                "evidence_refs": request.evidence_refs,
                "required_roles": ("baseline-owner",),
                "protected_authority": True,
            },
        )
        return {
            "baseline_candidate": record.model_dump(mode="json"),
            "impact": list(request.scoped_head_map),
            "protected_authority_package": {
                "approved_digest": record.record_digest,
                "required_roles": ["baseline-owner"],
            },
        }

    async def baseline_decide(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = BaselineDecideInput.model_validate(value)
        require_authenticated_authority(
            request.project_id, request.actor_ref, request.role_assignment_ref
        )
        typed_candidate = next(
            (
                item
                for item in self._baselines.list_candidates(request.project_id)
                if item.candidate_id == request.baseline_candidate_id
            ),
            None,
        )
        if typed_candidate is not None:
            role = next(
                (
                    item
                    for item in self._governance.list_roles(request.project_id)
                    if item.role_assignment_id == request.role_assignment_ref
                    and item.actor_id == request.actor_ref
                    and item.role == "baseline-owner"
                    and item.state == "ACTIVE"
                ),
                None,
            )
            if role is None:
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED,
                    "baseline authority role missing",
                )
            try:
                decision, baseline = self._baselines.decide(
                    project_id=request.project_id,
                    candidate_id=request.baseline_candidate_id,
                    decision=request.decision,
                    actor_ref=request.actor_ref,
                    role_assignment_ref=request.role_assignment_ref,
                    approved_digest=request.approved_digest,
                )
            except ValueError as exc:
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
            return {
                "typed_baseline_decision": decision.model_dump(mode="json"),
                "typed_baseline_set": (
                    None if baseline is None else baseline.model_dump(mode="json")
                ),
                "protected_decision": True,
                "current_state_mutated": baseline is not None,
            }
        candidate = self._records.read(
            request.project_id, "REVISION", request.baseline_candidate_id
        )
        if candidate is None or candidate.record_type != "BASELINE_CANDIDATE":
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "candidate not found")
        if candidate.record_digest != request.approved_digest:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "approved digest mismatch")
        role = next(
            (
                item
                for item in self._governance.list_roles(request.project_id)
                if item.role_assignment_id == request.role_assignment_ref
                and item.actor_id == request.actor_ref
                and item.role == "baseline-owner"
                and item.state == "ACTIVE"
            ),
            None,
        )
        if role is None:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "baseline authority role missing"
            )
        if request.decision == "REJECT":
            rejected = self._controls.create(
                project_id=request.project_id,
                namespace="REVISION",
                record_type="BASELINE_CANDIDATE",
                record_id=candidate.record_id,
                state="REJECTED",
                payload={**candidate.payload, "reason": request.reason},
            )
            return {"baseline_candidate": rejected.model_dump(mode="json")}
        prior = self._records.list(request.project_id, "REVISION", "BASELINE")
        for item in prior:
            if item.state == "CURRENT":
                self._controls.create(
                    project_id=request.project_id,
                    namespace="REVISION",
                    record_type="BASELINE",
                    record_id=item.record_id,
                    state="SUPERSEDED",
                    payload=item.payload,
                )
        baseline = self._controls.create(
            project_id=request.project_id,
            namespace="REVISION",
            record_type="BASELINE",
            state="CURRENT",
            payload={
                **candidate.payload,
                "authority": {
                    "actor_ref": request.actor_ref,
                    "role_assignment_ref": request.role_assignment_ref,
                },
                "candidate_digest": candidate.record_digest,
            },
        )
        return cast(
            dict[str, JsonValue],
            {
                "baseline": baseline.model_dump(mode="json"),
                "immutable_manifest": baseline.payload.get("scoped_head_map"),
            },
        )

    def _record(self, request: RecordReadInput, record_type: str) -> ControlRecord:
        item = self._records.read(request.project_id, "REVISION", request.record_id)
        if item is None or item.record_type != record_type:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, f"{record_type} not found")
        return item

    def _revision_snapshot(self, project_id: str, digest: str) -> EntitySnapshot:
        revision = self._ledger.read_revision_by_digest(project_id, digest)
        snapshot = None if revision is None else self._ledger.read_snapshot(revision.snapshot_id)
        if revision is None or snapshot is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "revision content not found")
        return snapshot

    def _staged_revision(self, proposal: ControlRecord) -> StagedRevision:
        payload = proposal.payload
        entity_type = EntityType(str(payload["aggregate_type"]))
        entity_id = str(payload["aggregate_id"])
        content = self._dict_object(payload.get("candidate_content"))
        snapshot = EntitySnapshot(
            snapshot_id=self._ids.new("snapshot"),
            project_id=proposal.project_id,
            entity_type=entity_type,
            entity_id=entity_id,
            schema_version=str(payload.get("candidate_schema_version", "1.0.0")),
            content=content,
            content_digest=str(payload["content_digest"]),
        )
        actor_ref = str(payload.get("actor_or_agent_ref", "agent:revision-proposer"))
        actor = ActorRef(
            actor_id=actor_ref,
            kind=ActorKind.AGENT if actor_ref.startswith("agent:") else ActorKind.HUMAN,
            role=str(payload.get("authenticated_role") or "revision-proposer"),
            project_id=proposal.project_id,
            session_id=(
                None
                if payload.get("authenticated_session_id") is None
                else str(payload.get("authenticated_session_id"))
            ),
            role_assignment_ref=(
                None
                if payload.get("authenticated_role_assignment_ref") is None
                else str(payload.get("authenticated_role_assignment_ref"))
            ),
        )
        parents = self._strings(payload.get("parent_revision_digests"))
        revision_payload = {
            "project_id": proposal.project_id,
            "entity_type": entity_type.value,
            "entity_id": entity_id,
            "content_digest": snapshot.content_digest,
            "parents": parents,
            "reason": payload.get("reason"),
            "proposal_digest": proposal.record_digest,
        }
        revision = SemanticRevision(
            revision_id=self._ids.new("revision"),
            project_id=proposal.project_id,
            entity_type=entity_type,
            entity_id=entity_id,
            snapshot_id=snapshot.snapshot_id,
            parent_revision_digests=parents,
            actor=actor,
            reason=str(payload.get("reason", "proposed revision")),
            evidence_refs=self._strings(payload.get("evidence_refs")),
            affected_refs=(),
            revision_digest=domain_digest(
                "SEMANTIC_REVISION",
                "1.0.0",
                canonical_payload(revision_payload),
            ),
            created_at=self._clock.now(),
        )
        return StagedRevision(snapshot=snapshot, revision=revision)

    def _open_conflict(self, project_id: str, *, aggregate_id: str, payload: dict[str, object]):
        return self._controls.create(
            project_id=project_id,
            namespace="REVISION",
            record_type="CONFLICT",
            state="OPEN_CONFLICT",
            payload={"aggregate_id": aggregate_id, **payload},
        )

    def _all_revisions(self, project_id: str) -> tuple[SemanticRevision, ...]:
        return history_revisions(self._ledger, project_id, self._history_reader)

    def _head_for_aggregate(self, project_id: str, aggregate_id: str) -> str | None:
        for key, digest in self._ledger.read_heads(project_id).items():
            if key.split(":", 1)[-1] == aggregate_id:
                return digest
        return None

    @staticmethod
    def _branch_preservation_allowed(
        *,
        expected: dict[str, str],
        actual: dict[str, str],
        proposals: tuple[ControlRecord, ...],
    ) -> bool:
        changed_keys = {key for key, value in expected.items() if actual.get(key) != value}
        if not changed_keys or not proposals:
            return False
        proposal_keys: set[str] = set()
        for proposal in proposals:
            aggregate_type = str(proposal.payload.get("aggregate_type"))
            aggregate_id = str(proposal.payload.get("aggregate_id"))
            key = f"{aggregate_type}:{aggregate_id}"
            parents = RevisionFullHandlers._strings(proposal.payload.get("parent_revision_digests"))
            if key not in changed_keys or expected.get(key) not in parents:
                return False
            proposal_keys.add(key)
        return proposal_keys == changed_keys

    def _infer_entity_type(self, project_id: str, digest: str) -> str:
        revision = self._ledger.read_revision_by_digest(project_id, digest)
        if revision is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "revision not found")
        return revision.entity_type.value

    @staticmethod
    def _ancestor_digests(
        revisions: tuple[SemanticRevision, ...], root: str, depth: int
    ) -> set[str]:
        by_digest = {item.revision_digest: item for item in revisions}
        seen: set[str] = set()
        frontier = [(root, 0)]
        while frontier:
            digest, level = frontier.pop(0)
            if digest in seen or level > depth:
                continue
            seen.add(digest)
            item = by_digest.get(digest)
            if item is not None:
                frontier.extend((parent, level + 1) for parent in item.parent_revision_digests)
        return seen

    @staticmethod
    def _strings(value: object) -> tuple[str, ...]:
        if not isinstance(value, tuple | list):
            return ()
        return tuple(str(item) for item in cast(tuple[object, ...] | list[object], value))

    @staticmethod
    def _dict_str(value: object) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return {str(key): str(child) for key, child in cast(dict[object, object], value).items()}

    @staticmethod
    def _dict_object(value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            return {}
        return {str(key): child for key, child in cast(dict[object, object], value).items()}
