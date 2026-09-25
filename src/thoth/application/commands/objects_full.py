from __future__ import annotations

from typing import cast

from pydantic import Field, JsonValue

from thoth.application.services.decision_object_service import DecisionObjectService
from thoth.application.services.revision_service import CommitResult
from thoth.domain.auth import authenticated_data_scope_allows
from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.decision_object_full import DecisionObjectRecord
from thoth.ports.decision_object import DecisionObjectStorePort
from thoth.ports.dependency import DependencyGraphPort
from thoth.ports.thread import ThreadStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class ProjectInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class ObjectListInput(ProjectInput):
    thread_id: str | None = Field(default=None, max_length=160)
    lifecycle: str | None = Field(default=None, max_length=40)
    active_work_mode: str | None = Field(default=None, max_length=60)
    profile_ref: str | None = Field(default=None, max_length=160)
    attention: str | None = Field(default=None, max_length=60)
    blocker: str | None = Field(default=None, max_length=160)


class ObjectReadInput(ProjectInput):
    object_id: str = Field(min_length=1, max_length=160)
    revision_digest: str | None = Field(default=None, min_length=64, max_length=64)


class CandidateListInput(ProjectInput):
    thread_id: str | None = Field(default=None, max_length=160)
    candidate_state: str | None = Field(default=None, max_length=40)
    trigger_type: str | None = Field(default=None, max_length=80)


class CandidateReadInput(ProjectInput):
    candidate_id: str = Field(min_length=1, max_length=160)


class ProfileListInput(ProjectInput):
    domain_hint: str | None = Field(default=None, max_length=160)
    enabled_only: bool = True


class ProfileReadInput(ProjectInput):
    profile_ref: str = Field(min_length=1, max_length=160)
    version: int | None = Field(default=None, ge=1)


class RelationListInput(ObjectReadInput):
    relation_type: str | None = Field(default=None, max_length=80)
    target_namespace: str | None = Field(default=None, max_length=80)
    authority_state: str | None = Field(default=None, max_length=40)


class AttentionListInput(ProjectInput):
    attention_type: str | None = Field(default=None, max_length=80)
    risk: str | None = Field(default=None, max_length=40)


class AuditReadInput(ObjectReadInput):
    pass


class MaterializeInput(ProjectInput):
    thread_id: str = Field(min_length=1, max_length=160)
    candidate_id: str | None = Field(default=None, max_length=160)
    purpose_statement: str = Field(min_length=1, max_length=2_000)
    problem_frame: str | None = Field(default=None, max_length=5_000)
    focus_refs: tuple[str, ...]
    trigger_evidence_refs: tuple[str, ...]
    profile_refs: tuple[str, ...] = ()
    expected_project_revision: int | None = Field(default=None, ge=0)
    actor_ref: str = Field(default="agent:object-materializer", max_length=160)


class RevisionBoundInput(ObjectReadInput):
    expected_revision_digest: str = Field(min_length=64, max_length=64)


class FrameReviseInput(RevisionBoundInput):
    frame_patch: dict[str, JsonValue]
    evidence_refs: tuple[str, ...]
    reason: str = Field(min_length=1, max_length=5_000)


class ProfileApplyInput(RevisionBoundInput):
    profile_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    reason: str = Field(min_length=1, max_length=5_000)


class FacetUpdateInput(RevisionBoundInput):
    add: tuple[str, ...] = ()
    remove: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...]
    reason: str = Field(min_length=1, max_length=5_000)


class RelationAddInput(RevisionBoundInput):
    relation_type: str = Field(min_length=1, max_length=80)
    target_ref: str = Field(min_length=1, max_length=260)
    semantic_role: str = Field(min_length=1, max_length=260)
    evidence_refs: tuple[str, ...]
    valid_time: str | None = Field(default=None, max_length=64)
    authority_state: str = Field(pattern=r"^(UNCLASSIFIED|INFORMAL|OFFICIAL|APPROVED)$")
    actor_ref: str = Field(default="agent:relation-proposer", max_length=160)


class RelationRemoveInput(RevisionBoundInput):
    relation_id: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=5_000)
    evidence_refs: tuple[str, ...]


class RevalidateInput(ObjectReadInput):
    trigger_reason: str = Field(min_length=1, max_length=2_000)


class WorkReplanInput(ObjectReadInput):
    trigger_refs: tuple[str, ...]
    budget_policy_ref: str | None = Field(default=None, max_length=160)


class SplitProposeInput(RevisionBoundInput):
    partitions: tuple[dict[str, JsonValue], ...]
    evidence_refs: tuple[str, ...]
    rationale: str = Field(min_length=1, max_length=5_000)


class MergeProposeInput(ProjectInput):
    object_ids: tuple[str, ...] = Field(min_length=2)
    field_mapping: dict[str, JsonValue]
    evidence_refs: tuple[str, ...]
    rationale: str = Field(min_length=1, max_length=5_000)
    expected_revision_digests: tuple[str, ...] = Field(min_length=2)


class AttentionAcknowledgeInput(RevisionBoundInput):
    attention_id: str = Field(min_length=1, max_length=160)
    actor_ref: str = Field(min_length=1, max_length=160)
    note: str | None = Field(default=None, max_length=2_000)


class FollowupCreateInput(RevisionBoundInput):
    purpose_statement: str = Field(min_length=1, max_length=2_000)
    trigger_refs: tuple[str, ...]
    inherit_scope: bool = True


class DecisionObjectHandlers:
    def __init__(
        self,
        *,
        store: DecisionObjectStorePort,
        service: DecisionObjectService,
        dependencies: DependencyGraphPort,
        threads: ThreadStorePort,
    ) -> None:
        self._store = store
        self._service = service
        self._dependencies = dependencies
        self._threads = threads

    def authorize_before_claim(self, method: str, value: dict[str, JsonValue]) -> None:
        if method != "object/materialize":
            return
        identifier = value.get("thread_id")
        thread = self._threads.read(identifier) if isinstance(identifier, str) else None
        if (
            thread is not None
            and thread.project_id == value.get("project_id")
            and not authenticated_data_scope_allows(thread.scope)
        ):
            raise RpcApplicationError(
                RpcErrorCode.AUTHORIZATION_DENIED,
                "stored Thread workstream is outside the authenticated data scope",
                data={"reason_code": "AUTH_DATA_SCOPE_DENIED", "pre_io": True},
            )

    async def list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ObjectListInput.model_validate(value)
        items = tuple(
            item
            for item in self._store.list_objects(request.project_id)
            if (request.thread_id is None or item.thread_id == request.thread_id)
            and (request.lifecycle is None or item.lifecycle == request.lifecycle)
            and (
                request.active_work_mode is None
                or item.active_work_mode == request.active_work_mode
            )
            and (request.profile_ref is None or request.profile_ref in item.profile_refs)
            and (request.attention is None or item.attention == request.attention)
            and (request.blocker is None or request.blocker in item.blockers)
        )
        return {"objects": [self._summary(item) for item in items], "next_cursor": None}

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ObjectReadInput.model_validate(value)
        item = self._read(request)
        return cast(
            dict[str, JsonValue],
            {
                "object": item.model_dump(mode="json"),
                "relations": [
                    relation.model_dump(mode="json")
                    for relation in self._store.list_relations(
                        request.project_id, request.object_id
                    )
                ],
                "attention_items": [
                    attention.model_dump(mode="json")
                    for attention in self._store.list_attention(
                        request.project_id, request.object_id
                    )
                ],
            },
        )

    async def candidate_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = CandidateListInput.model_validate(value)
        items = tuple(
            item
            for item in self._store.list_candidates(request.project_id, request.thread_id)
            if (request.candidate_state is None or item.candidate_state == request.candidate_state)
            and (request.trigger_type is None or item.trigger_type == request.trigger_type)
        )
        return {
            "candidates": [item.model_dump(mode="json") for item in items],
            "next_cursor": None,
        }

    async def candidate_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = CandidateReadInput.model_validate(value)
        item = self._store.read_candidate(request.candidate_id)
        if item is None or item.project_id != request.project_id:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "candidate not found")
        return {"candidate": item.model_dump(mode="json")}

    async def profile_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProfileListInput.model_validate(value)
        items = self._store.list_profiles(request.enabled_only)
        if request.domain_hint is not None:
            items = tuple(item for item in items if item.domain_hint == request.domain_hint)
        return {
            "profiles": [item.model_dump(mode="json") for item in items],
            "next_cursor": None,
        }

    async def profile_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProfileReadInput.model_validate(value)
        item = self._store.read_profile(request.profile_ref, request.version)
        if item is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "profile not found")
        return {"profile": item.model_dump(mode="json")}

    async def relation_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RelationListInput.model_validate(value)
        self._read(request)
        items = tuple(
            item
            for item in self._store.list_relations(request.project_id, request.object_id)
            if (request.relation_type is None or item.relation_type == request.relation_type)
            and (
                request.target_namespace is None
                or item.target_ref.startswith(f"{request.target_namespace}:")
            )
            and (request.authority_state is None or item.authority_state == request.authority_state)
        )
        return {"relations": [item.model_dump(mode="json") for item in items]}

    async def impact_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ObjectReadInput.model_validate(value)
        item = self._read(request)
        source_ref = f"OBJECT:{item.object_id}"
        downstream = self._dependencies.downstream(request.project_id, source_ref)
        states = self._dependencies.read_states(request.project_id)
        return {
            "impact": {
                "source_ref": source_ref,
                "direct_relation_targets": [
                    relation.target_ref
                    for relation in self._store.list_relations(
                        request.project_id, request.object_id
                    )
                    if relation.active
                ],
                "transitive_affected_refs": list(downstream),
                "dependency_states": {
                    key: state.value for key, state in states.items() if key in downstream
                },
                "recalculation_plan": [key for key in downstream if states.get(key) is not None],
            }
        }

    async def attention_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = AttentionListInput.model_validate(value)
        items = tuple(
            item
            for item in self._store.list_attention(request.project_id, None)
            if (request.attention_type is None or item.attention_type == request.attention_type)
            and (request.risk is None or item.risk == request.risk)
        )
        return {"attention_items": [item.model_dump(mode="json") for item in items]}

    async def audit_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = AuditReadInput.model_validate(value)
        self._read(request)
        return {
            "records": [
                item.model_dump(mode="json")
                for item in self._store.list_audit(request.project_id, request.object_id)
            ]
        }

    async def materialize(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        with self._service.atomic():
            self.authorize_before_claim("object/materialize", value)
            request = MaterializeInput.model_validate(value)
            thread = self._threads.read(request.thread_id)
            if thread is None or thread.project_id != request.project_id:
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "thread not found")
            try:
                candidate, item, commit = self._service.materialize(
                    project_id=request.project_id,
                    thread_id=request.thread_id,
                    purpose_statement=request.purpose_statement,
                    problem_frame=request.problem_frame or thread.problem,
                    focus_refs=request.focus_refs,
                    trigger_evidence_refs=request.trigger_evidence_refs,
                    profile_refs=request.profile_refs,
                    actor_ref=request.actor_ref,
                    workstream_refs=tuple(thread.scope.values()),
                )
            except ValueError as exc:
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
            if item is not None and item.object_id not in thread.current_object_ids:
                updated = thread.model_copy(
                    update={
                        "current_object_ids": (*thread.current_object_ids, item.object_id),
                        "revision": thread.revision + 1,
                    }
                )
                if not self._threads.update(updated, expected_revision=thread.revision):
                    raise RpcApplicationError(
                        RpcErrorCode.DOMAIN_REJECTED,
                        "thread revision changed while attaching Object",
                    )
            return {
                "candidate": candidate.model_dump(mode="json"),
                "object": None if item is None else item.model_dump(mode="json"),
                "commit": None if commit is None else commit.model_dump(mode="json"),
            }

    async def frame_revise(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = FrameReviseInput.model_validate(value)
        current = self._read_expected(request)
        allowed = {
            "purpose_statement",
            "problem_frame",
            "focus_refs",
            "workstream_refs",
            "requirement_refs",
            "cutoff_ref",
            "security_scope",
        }
        rejected = sorted(set(request.frame_patch) - allowed)
        if rejected:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                f"frame patch contains noncanonical fields: {', '.join(rejected)}",
            )
        updates: dict[str, object] = {
            key: tuple(child) if key.endswith("_refs") and isinstance(child, list) else child
            for key, child in request.frame_patch.items()
        }
        revised, commit = self._revise(
            current,
            updates=updates,
            event_type="object/updated",
            evidence_refs=request.evidence_refs,
        )
        return self._revision_result(current, revised, commit)

    async def profile_apply(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProfileApplyInput.model_validate(value)
        current = self._read_expected(request)
        profiles = tuple(
            self._store.read_profile(profile_ref, None) for profile_ref in request.profile_refs
        )
        if any(item is None or not item.enabled for item in profiles):
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "profile unavailable")
        required_before = self._required_fields(current.profile_refs)
        required_after = tuple(
            dict.fromkeys(
                field for item in profiles if item is not None for field in item.required_fields
            )
        )
        revised, commit = self._revise(
            current,
            updates={
                "profile_refs": request.profile_refs,
                "profile_state": "MATCHED" if len(request.profile_refs) == 1 else "AMBIGUOUS",
                "freshness": "STALE",
            },
            event_type="object/profileChanged",
            evidence_refs=request.evidence_refs,
        )
        result = self._revision_result(current, revised, commit)
        result["required_field_delta"] = cast(
            JsonValue,
            {
                "added": sorted(set(required_after) - set(required_before)),
                "removed": sorted(set(required_before) - set(required_after)),
            },
        )
        result["invalidations"] = cast(JsonValue, ["profile-dependent projections"])
        return result

    async def facet_update(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = FacetUpdateInput.model_validate(value)
        current = self._read_expected(request)
        allowed = {
            facet
            for profile_ref in current.profile_refs
            if (profile := self._store.read_profile(profile_ref, None)) is not None
            for facet in profile.allowed_facets
        }
        if not allowed:
            allowed = {
                "TECHNICAL",
                "EVIDENCE",
                "COST",
                "SCHEDULE",
                "SAFETY",
                "INTERFACE",
                "DATA",
                "MODEL",
            }
        accepted = tuple(
            sorted((set(current.facets) | (set(request.add) & allowed)) - set(request.remove))
        )
        rejected = tuple(sorted(set(request.add) - allowed))
        revised, commit = self._revise(
            current,
            updates={"facets": accepted},
            event_type="object/updated",
            evidence_refs=request.evidence_refs,
        )
        result = self._revision_result(current, revised, commit)
        result["accepted_facets"] = list(accepted)
        result["rejected_extensions"] = list(rejected)
        return result

    async def relation_add(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RelationAddInput.model_validate(value)
        current = self._read_expected(request)
        try:
            relation, revised, commit = self._service.add_relation(
                current,
                relation_type=request.relation_type,
                target_ref=request.target_ref,
                semantic_role=request.semantic_role,
                evidence_refs=request.evidence_refs,
                authority_state=request.authority_state,
                actor_ref=request.actor_ref,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {
            "relation": relation.model_dump(mode="json"),
            "object": revised.model_dump(mode="json"),
            "dependent_impact": list(
                self._dependencies.downstream(request.project_id, f"OBJECT:{request.object_id}")
            ),
            "commit": commit.model_dump(mode="json"),
        }

    async def relation_remove(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RelationRemoveInput.model_validate(value)
        current = self._read_expected(request)
        relation = self._store.read_relation(request.relation_id)
        if (
            relation is None
            or relation.project_id != request.project_id
            or relation.source_object_id != request.object_id
        ):
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "relation not found")
        try:
            ended, revised, commit = self._service.end_relation(
                current,
                relation,
                reason=request.reason,
                evidence_refs=request.evidence_refs,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {
            "relation": ended.model_dump(mode="json"),
            "object": revised.model_dump(mode="json"),
            "commit": commit.model_dump(mode="json"),
        }

    async def revalidate(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RevalidateInput.model_validate(value)
        current = self._read(request)
        try:
            revised, commit = self._service.revalidate(current, request.trigger_reason)
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return cast(
            dict[str, JsonValue],
            {
                "object": revised.model_dump(mode="json"),
                "commit": commit.model_dump(mode="json"),
            },
        )

    async def work_replan(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = WorkReplanInput.model_validate(value)
        current = self._read(request)
        if current.blockers:
            mode = "ACQUIRING_EVIDENCE"
            missing = list(current.blockers)
            reason = "profile or evidence blocker remains"
        elif current.verification in {"PENDING", "INCONCLUSIVE", "FAILED"}:
            mode = "VERIFYING"
            missing = []
            reason = "verification needs resolution"
        elif current.resolution == "OPEN":
            mode = "INVESTIGATING"
            missing = []
            reason = "causal and action alternatives remain open"
        else:
            mode = "IDLE"
            missing = []
            reason = "no bounded work transition is currently justified"
        revised, commit = self._revise(
            current,
            updates={"active_work_mode": mode},
            event_type="object/workModeChanged",
        )
        return cast(
            dict[str, JsonValue],
            {
                "object": revised.model_dump(mode="json"),
                "proposed_active_work_mode": mode,
                "missing_inputs": missing,
                "bounded_next_plan": {
                    "trigger_refs": list(request.trigger_refs),
                    "budget_policy_ref": request.budget_policy_ref,
                    "allowed": mode != "IDLE",
                },
                "hold_reason": reason,
                "commit": commit.model_dump(mode="json"),
            },
        )

    async def split_propose(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = SplitProposeInput.model_validate(value)
        current = self._read_expected(request)
        payload: dict[str, JsonValue] = {
            "project_id": request.project_id,
            "object_id": request.object_id,
            "partitions": [dict(item) for item in request.partitions],
            "evidence_refs": list(request.evidence_refs),
            "rationale": request.rationale,
            "expected_revision_digest": request.expected_revision_digest,
            "applied": False,
            "owner_namespace": "REVISION",
        }
        digest = domain_digest("OBJECT_SPLIT_PROPOSAL", "1.0.0", canonical_payload(payload))
        self._service.audit(current, "object/splitProposed", {**payload, "digest": digest})
        return {
            "split_proposal": {**payload, "digest": digest},
            "lineage_preview": [f"PART_OF:{request.object_id}"],
            "impact_preview": ["dependent projections become stale after application"],
        }

    async def merge_propose(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = MergeProposeInput.model_validate(value)
        if len(request.object_ids) != len(request.expected_revision_digests):
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                "object IDs and expected revision digests must align",
            )
        objects = tuple(
            self._store.read_object(request.project_id, object_id, None)
            for object_id in request.object_ids
        )
        if any(item is None for item in objects):
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "merge object not found")
        actual = tuple(item.revision_digest for item in objects if item is not None)
        if actual != request.expected_revision_digests:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "merge revision mismatch")
        conflicts = sorted(
            {
                "problem_frame"
                for item in objects[1:]
                if item is not None
                and objects[0] is not None
                and item.problem_frame != objects[0].problem_frame
            }
        )
        payload: dict[str, JsonValue] = {
            "project_id": request.project_id,
            "object_ids": list(request.object_ids),
            "field_mapping": request.field_mapping,
            "evidence_refs": list(request.evidence_refs),
            "rationale": request.rationale,
            "expected_revision_digests": list(request.expected_revision_digests),
            "applied": False,
            "owner_namespace": "REVISION",
            "conflicts": cast(JsonValue, conflicts),
        }
        digest = domain_digest("OBJECT_MERGE_PROPOSAL", "1.0.0", canonical_payload(payload))
        first = cast(DecisionObjectRecord, objects[0])
        self._service.audit(first, "object/mergeProposed", {**payload, "digest": digest})
        return {
            "merge_proposal": {**payload, "digest": digest},
            "lineage_preview": [f"SUPERSEDES:{item}" for item in request.object_ids],
            "impact_preview": ["application belongs to Revision namespace"],
        }

    async def attention_acknowledge(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = AttentionAcknowledgeInput.model_validate(value)
        current = self._read_expected(request)
        attention = self._store.read_attention(request.attention_id)
        if (
            attention is None
            or attention.project_id != request.project_id
            or attention.object_id != request.object_id
        ):
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "attention not found")
        revised_attention, revised, commit = self._service.acknowledge_attention(
            current,
            attention,
            actor_ref=request.actor_ref,
            note=request.note,
        )
        return {
            "attention": revised_attention.model_dump(mode="json"),
            "object": revised.model_dump(mode="json"),
            "commit": commit.model_dump(mode="json"),
        }

    async def followup_create(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = FollowupCreateInput.model_validate(value)
        current = self._read_expected(request)
        try:
            candidate, child, commit = self._service.materialize(
                project_id=request.project_id,
                thread_id=current.thread_id,
                purpose_statement=request.purpose_statement,
                problem_frame=request.purpose_statement,
                focus_refs=(f"OBJECT:{current.object_id}",),
                trigger_evidence_refs=request.trigger_refs,
                profile_refs=current.profile_refs,
                actor_ref="agent:followup-materializer",
                parent_object_id=current.object_id,
                trigger_type="FOLLOWUP",
                workstream_refs=current.workstream_refs if request.inherit_scope else (),
                requirement_refs=current.requirement_refs if request.inherit_scope else (),
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        if child is None or commit is None:
            return {
                "candidate": candidate.model_dump(mode="json"),
                "object": None,
                "commit": None,
            }
        relation, child_revised, relation_commit = self._service.add_relation(
            child,
            relation_type="FOLLOWUP_OF",
            target_ref=f"OBJECT:{current.object_id}",
            semantic_role="explicit follow-up lineage",
            evidence_refs=request.trigger_refs,
            authority_state="OFFICIAL",
            actor_ref="agent:followup-materializer",
        )
        thread = self._threads.read(current.thread_id)
        if thread is not None and child.object_id not in thread.current_object_ids:
            self._threads.update(
                thread.model_copy(
                    update={
                        "current_object_ids": (*thread.current_object_ids, child.object_id),
                        "revision": thread.revision + 1,
                    }
                ),
                expected_revision=thread.revision,
            )
        return {
            "candidate": candidate.model_dump(mode="json"),
            "object": child_revised.model_dump(mode="json"),
            "lineage": relation.model_dump(mode="json"),
            "commit": relation_commit.model_dump(mode="json"),
        }

    def _read(self, request: ObjectReadInput) -> DecisionObjectRecord:
        item = self._store.read_object(
            request.project_id, request.object_id, request.revision_digest
        )
        if item is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "object not found")
        return item

    def _read_expected(self, request: RevisionBoundInput) -> DecisionObjectRecord:
        current = self._store.read_object(request.project_id, request.object_id, None)
        if current is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "object not found")
        if current.revision_digest != request.expected_revision_digest:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "object revision changed concurrently"
            )
        return current

    def _revise(
        self,
        current: DecisionObjectRecord,
        *,
        updates: dict[str, object],
        event_type: str,
        evidence_refs: tuple[str, ...] = (),
    ) -> tuple[DecisionObjectRecord, CommitResult]:
        try:
            return self._service.revise(
                current,
                updates=updates,
                event_type=event_type,
                evidence_refs=evidence_refs,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc

    def _required_fields(self, profile_refs: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                field
                for profile_ref in profile_refs
                if (profile := self._store.read_profile(profile_ref, None)) is not None
                for field in profile.required_fields
            )
        )

    @staticmethod
    def _summary(item: DecisionObjectRecord) -> dict[str, JsonValue]:
        return {
            "object_id": item.object_id,
            "project_id": item.project_id,
            "thread_id": item.thread_id,
            "purpose_statement": item.purpose_statement,
            "lifecycle": item.lifecycle,
            "active_work_mode": item.active_work_mode,
            "profile_refs": list(item.profile_refs),
            "profile_state": item.profile_state,
            "attention": item.attention,
            "blockers": list(item.blockers),
            "resolution": item.resolution,
            "verification": item.verification,
            "disposition": item.disposition,
            "freshness": item.freshness,
            "working_head_digest": item.revision_digest,
        }

    @staticmethod
    def _revision_result(
        before: DecisionObjectRecord, after: DecisionObjectRecord, commit: CommitResult
    ) -> dict[str, JsonValue]:
        before_data = before.model_dump(mode="json")
        after_data = after.model_dump(mode="json")
        diff: list[JsonValue] = [
            cast(
                JsonValue,
                {
                    "path": key,
                    "before": before_data.get(key),
                    "after": after_data.get(key),
                },
            )
            for key in sorted(set(before_data) | set(after_data))
            if before_data.get(key) != after_data.get(key)
        ]
        commit_value = cast(JsonValue, commit.model_dump(mode="json"))
        return cast(
            dict[str, JsonValue],
            {
                "object": after.model_dump(mode="json"),
                "semantic_diff": diff,
                "affected_relations": list(after.relation_refs),
                "invalidations": [],
                "status_axes": {
                    "lifecycle": after.lifecycle,
                    "resolution": after.resolution,
                    "verification": after.verification,
                    "disposition": after.disposition,
                    "freshness": after.freshness,
                    "attention": after.attention,
                },
                "commit": commit_value,
            },
        )
