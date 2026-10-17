from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.application.commands import revision_command_drafts as drafts
from thoth.application.commands import revision_command_views as views
from thoth.application.commands import revision_publication_records as publication
from thoth.application.commands import revision_publication_views as publication_views
from thoth.application.commands.revision_read_projection import (
    revision_ancestor_digests,
    revision_diff_view,
    revision_graph_view,
    revision_impact_view,
)
from thoth.application.commands.revisions_inputs import (
    AuditReadInput,
    BaselineDecideInput,
    BaselineListInput,
    BaselinePrepareInput,
    BaselineReadInput,
    BranchCreateInput,
    ChangeSetCreateInput,
    ConflictListInput,
    ContentReadInput,
    DiffReadInput,
    GraphReadInput,
    HeadReadInput,
    MergeProposeInput,
    MergeResolveInput,
    ProposeInput,
    RecomputeRequestInput,
    RecordReadInput,
    RestorePreviewInput,
    RevisionListInput,
    RevisionReadInput,
)
from thoth.application.commands.revisions_inputs import ChangeSetCommitInput as ChangeSetCommitInput
from thoth.application.commands.revisions_inputs import (
    ChangeSetValidateInput as ChangeSetValidateInput,
)
from thoth.application.commands.revisions_inputs import ProjectInput as ProjectInput
from thoth.application.commands.revisions_inputs import RestoreProposeInput as RestoreProposeInput
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
from thoth.domain.auth import (
    bind_authenticated_actor,
    current_authenticated_actor,
    require_authenticated_authority,
)
from thoth.domain.canonical import canonical_payload, domain_digest, head_set_digest
from thoth.domain.control_record import ControlRecord
from thoth.domain.enums import EntityType
from thoth.domain.revision import (
    EntitySnapshot,
    ImpactPropagationPlan,
    RevisionChangeSet,
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
                return publication_views.content_response(snapshot, revision)
        raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "content not found")

    async def diff_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = DiffReadInput.model_validate(value)
        left = self._revision_snapshot(request.project_id, request.from_revision_digest)
        right = self._revision_snapshot(request.project_id, request.to_revision_digest)
        changes = semantic_diff(left.content, right.content)
        return revision_diff_view(changes)

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
        branches = self._records.list(request.project_id, "REVISION", "BRANCH")
        return revision_graph_view(revisions, branches)

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
        return revision_impact_view(change_set, downstream)

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
        return publication_views.restore_preview_response(snapshot, current, target, affected)

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
            **drafts.proposal_fields(request, content, content_digest),
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
            **views.proposal_status_fields(record, content_digest),
            "impact_preview": list(self._dependencies.downstream(request.project_id, key)),
        }

    async def change_set_create(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ChangeSetCreateInput.model_validate(value)
        missing = tuple(
            digest
            for digest in request.candidate_revision_digests
            if self._records.read_digest(request.project_id, digest) is None
        )
        payload = drafts.change_set_payload(request, missing)
        record = self._controls.create(
            project_id=request.project_id,
            namespace="REVISION",
            record_type="CHANGE_SET",
            state="STAGED" if not missing else "HELD",
            payload=payload,
        )
        return views.change_set_created(record, missing, payload)

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
        checks, ready, validation_bundle, validation_digest = drafts.validation_summary(
            proposals, optimistic, branch_preservation
        )
        payload = drafts.validated_payload(current, validation_bundle, validation_digest)
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
        return views.validation_response(revised, checks, validation_bundle, validation_digest)

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
            domain_transitions.extend(publication.domain_transition_names(entity_type, content))
            staged.append(self._staged_revision(proposal))
        authenticated = current_authenticated_actor()
        actor = publication.commit_actor(authenticated, request)
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
        state, branch_revision_digests = publication.commit_outcome(result, staged)
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
            payload=drafts.branch_payload(request, actor_ref),
        )
        return {"branch": record.model_dump(mode="json"), "content_mutated": False}

    async def merge_propose(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = MergeProposeInput.model_validate(value)
        if len(request.parent_revision_digests) != 2:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                "semantic merge requires exactly two branch revisions",
            )
        with self._ledger.transaction():
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
                payload=publication.merge_proposal_payload(request, assessment, conflicts),
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
                **publication.merge_receipt_fields(request, assessment),
                "recorded_at": self._clock.now(),
            }
            receipt = publication.seal_merge_receipt(receipt_draft)
            self._controls.create(
                project_id=request.project_id,
                namespace="REVISION",
                record_type="MERGE_RECEIPT",
                record_id=receipt.receipt_id,
                state=assessment.state.value,
                payload=receipt.model_dump(mode="python"),
            )
            return publication_views.merge_response(record, conflict, assessment, receipt)

    async def merge_resolve(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = MergeResolveInput.model_validate(value)
        actor_ref = bind_authenticated_actor(request.project_id, request.actor_or_agent_ref)
        with self._ledger.transaction():
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
                    "expected_head_digest": self._head_for_aggregate(
                        request.project_id, aggregate_id
                    ),
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
                    **publication.resolved_change_fields(revision),
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
            return publication_views.resolved_response(resolved, revision, change_set)

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
                **publication.restore_proposal_fields(request, current, target_snapshot, content),
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
            payload=drafts.recompute_payload(request),
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
            payload=drafts.baseline_candidate_payload(request, manifest_digest),
        )
        return views.baseline_candidate_response(record, request)

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
            return publication_views.typed_baseline_response(decision, baseline)
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
        with self._ledger.transaction():
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
                payload=publication.legacy_baseline_payload(candidate, request),
            )
            return publication_views.baseline_response(baseline)

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
        actor = publication.staged_actor(actor_ref, payload, proposal)
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

    _ancestor_digests = staticmethod(revision_ancestor_digests)

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
