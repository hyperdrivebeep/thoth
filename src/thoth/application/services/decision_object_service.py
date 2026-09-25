from __future__ import annotations

from contextlib import AbstractContextManager

from thoth.application.services.revision_service import CommitResult, RevisionCommitService
from thoth.domain.actor import ActorRef
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.decision_object_full import (
    DecisionObjectRecord,
    ObjectAttentionRecord,
    ObjectAuditRecord,
    ObjectCandidateRecord,
    ObjectProfileRecord,
    ObjectRelationRecord,
)
from thoth.domain.enums import ActorKind, EntityType
from thoth.domain.relation import DependencyRelation
from thoth.domain.revision import (
    EntitySnapshot,
    ImpactPropagationPlan,
    RevisionChangeSet,
    SemanticRevision,
    StagedRevision,
)
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.decision_object import DecisionObjectStorePort
from thoth.ports.dependency import DependencyGraphPort
from thoth.ports.ledger import LedgerPort, LedgerTransactionPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort

BUILTIN_OBJECT_PROFILES = (
    (
        "GENERAL_RND_DECISION",
        "General R&D decision",
        "general-rnd",
        ("purpose_statement", "problem_frame", "focus_refs"),
        (
            "TECHNICAL",
            "EVIDENCE",
            "COST",
            "SCHEDULE",
            "SAFETY",
            "INTERFACE",
            "DATA",
            "MODEL",
        ),
    ),
    (
        "SYSTEMS_INTEGRATION",
        "Systems integration case",
        "systems-engineering",
        ("purpose_statement", "problem_frame", "focus_refs", "workstream_refs"),
        ("TECHNICAL", "EVIDENCE", "INTERFACE", "SAFETY", "SCHEDULE"),
    ),
    (
        "EXPERIMENT_DIAGNOSIS",
        "Experiment diagnosis case",
        "experimental-rnd",
        ("purpose_statement", "problem_frame", "focus_refs"),
        ("TECHNICAL", "EVIDENCE", "DATA", "MODEL", "SAFETY"),
    ),
)

ALLOWED_RELATIONS = (
    "PART_OF",
    "DEPENDS_ON",
    "BLOCKED_BY",
    "VERIFIES",
    "QUALIFIES",
    "CONTRADICTS",
    "FOLLOWUP_OF",
    "SUPERSEDES",
)


class DecisionObjectService:
    def atomic(self) -> AbstractContextManager[LedgerTransactionPort]:
        return self._ledger.transaction()

    def __init__(
        self,
        *,
        store: DecisionObjectStorePort,
        artifacts: ArtifactLedgerPort,
        dependencies: DependencyGraphPort,
        ledger: LedgerPort,
        commits: RevisionCommitService,
        clock: ClockPort,
        ids: IdGeneratorPort,
    ) -> None:
        self._store = store
        self._artifacts = artifacts
        self._dependencies = dependencies
        self._ledger = ledger
        self._commits = commits
        self._clock = clock
        self._ids = ids

    def seed_profiles(self) -> None:
        with self._ledger.transaction():
            for profile_ref, name, domain_hint, required, facets in BUILTIN_OBJECT_PROFILES:
                draft: dict[str, object] = {
                    "profile_ref": profile_ref,
                    "version": 1,
                    "name": name,
                    "domain_hint": domain_hint,
                    "namespace_owner": "THOTH.OBJECT",
                    "required_fields": required,
                    "conditional_fields": (
                        "requirement_refs",
                        "cutoff_ref",
                        "protected_boundary_context",
                    ),
                    "allowed_facets": facets,
                    "allowed_relation_types": ALLOWED_RELATIONS,
                    "entry_policy": {
                        "trigger": "DECISION_SENSITIVE_OR_EXPLICIT_USER_REQUEST",
                        "scope": "PROJECT_AND_THREAD_REQUIRED",
                        "evidence": "EVIDENCE_OR_TYPED_USER_ASSERTION",
                    },
                    "exit_policy": {
                        "resolution": "EXPLICIT",
                        "verification": "PROFILE_ACCEPTABLE",
                        "conflict": "NO_CLOSURE_BLOCKER",
                    },
                    "enabled": True,
                    "migration_state": "CURRENT",
                }
                self._store.put_profile(
                    ObjectProfileRecord.model_validate(
                        {
                            **draft,
                            "profile_digest": domain_digest(
                                "OBJECT_PROFILE", "1.0.0", canonical_payload(draft)
                            ),
                        }
                    )
                )

    def materialize(
        self,
        *,
        project_id: str,
        thread_id: str,
        purpose_statement: str,
        problem_frame: str,
        focus_refs: tuple[str, ...],
        trigger_evidence_refs: tuple[str, ...],
        profile_refs: tuple[str, ...],
        actor_ref: str,
        object_id: str | None = None,
        parent_object_id: str | None = None,
        trigger_type: str = "EXPLICIT_USER_REQUEST",
        workstream_refs: tuple[str, ...] = (),
        requirement_refs: tuple[str, ...] = (),
    ) -> tuple[ObjectCandidateRecord, DecisionObjectRecord | None, CommitResult | None]:
        with self._ledger.transaction():
            self._validate_evidence(project_id, trigger_evidence_refs)
            profiles = self._profiles(profile_refs)
            selected_refs = tuple(profile.profile_ref for profile in profiles)
            profile_state = (
                "MATCHED"
                if len(selected_refs) == 1
                else "AMBIGUOUS"
                if len(selected_refs) > 1
                else "UNCLASSIFIED"
            )
            duplicates = tuple(
                item.object_id
                for item in self._store.list_objects(project_id)
                if item.thread_id == thread_id
                and item.lifecycle not in {"CLOSED", "SUPERSEDED"}
                and item.purpose_statement.casefold() == purpose_statement.casefold()
            )
            entry_validation = (
                "PASS"
                if purpose_statement.strip()
                and problem_frame.strip()
                and focus_refs
                and (trigger_evidence_refs or trigger_type == "EXPLICIT_USER_REQUEST")
                else "FAIL"
            )
            transition = (
                "HOLD_DUPLICATE"
                if duplicates
                else "MATERIALIZE_ROUTINE"
                if entry_validation == "PASS"
                else "HOLD_ENTRY_INVALID"
            )
            candidate_draft: dict[str, object] = {
                "candidate_id": self._ids.new("object-candidate"),
                "project_id": project_id,
                "thread_id": thread_id,
                "purpose_statement": purpose_statement,
                "problem_frame": problem_frame,
                "focus_refs": focus_refs,
                "trigger_type": trigger_type,
                "trigger_evidence_refs": trigger_evidence_refs,
                "profile_candidates": selected_refs,
                "profile_state": profile_state,
                "duplicate_object_refs": duplicates,
                "entry_validation": entry_validation,
                "permitted_transition": transition,
                "candidate_state": "PROPOSED",
                "created_at": self._clock.now(),
            }
            candidate = ObjectCandidateRecord.model_validate(
                {
                    **candidate_draft,
                    "candidate_digest": domain_digest(
                        "OBJECT_CANDIDATE", "1.0.0", canonical_payload(candidate_draft)
                    ),
                }
            )
            self._store.add_candidate(candidate)
            if transition != "MATERIALIZE_ROUTINE":
                return candidate, None, None
            object_draft: dict[str, object] = {
                "object_revision_id": self._ids.new("object-revision"),
                "object_id": object_id or self._ids.new("object"),
                "project_id": project_id,
                "thread_id": thread_id,
                "parent_object_id": parent_object_id,
                "purpose_statement": purpose_statement,
                "problem_frame": problem_frame,
                "focus_refs": focus_refs,
                "workstream_refs": workstream_refs,
                "requirement_refs": requirement_refs,
                "profile_refs": selected_refs,
                "profile_state": profile_state,
                "facets": (),
                "materialization_trigger": trigger_type,
                "trigger_evidence_refs": trigger_evidence_refs,
                "entry_criteria": {"status": "PASS", "candidate_ref": candidate.candidate_id},
                "exit_criteria": {"status": "NOT_SATISFIED", "reason": "work remains open"},
                "active_work_mode": "FRAMING",
                "lifecycle": "ACTIVE",
                "resolution": "OPEN",
                "verification": "NOT_ASSESSED",
                "disposition": "NONE",
                "freshness": "CURRENT",
                "attention": "NONE",
                "blockers": (),
                "relation_refs": (),
                "actor_or_agent_ref": actor_ref,
                "protected_boundary_context": {},
                "created_at": self._clock.now(),
            }
            record = DecisionObjectRecord.model_validate(
                {**object_draft, "revision_digest": self._digest(object_draft)}
            )
            committed, commit = self._persist(record, "object/materialized")
            self.audit(
                committed,
                "object/candidateCreated",
                {
                    "candidate_id": candidate.candidate_id,
                    "candidate_digest": candidate.candidate_digest,
                },
            )
            return candidate, committed, commit

    def revise(
        self,
        current: DecisionObjectRecord,
        *,
        updates: dict[str, object],
        event_type: str,
        evidence_refs: tuple[str, ...] = (),
    ) -> tuple[DecisionObjectRecord, CommitResult]:
        with self._ledger.transaction():
            self._validate_evidence(current.project_id, evidence_refs)
            draft = current.model_dump(mode="python")
            draft.update(updates)
            draft.update(
                {
                    "object_revision_id": self._ids.new("object-revision"),
                    "supersedes_revision_digest": current.revision_digest,
                    "created_at": self._clock.now(),
                }
            )
            draft.pop("revision_digest", None)
            draft.pop("receipt_ref", None)
            revised = DecisionObjectRecord.model_validate(
                {**draft, "revision_digest": self._digest(draft)}
            )
            return self._persist(revised, event_type, evidence_refs=evidence_refs)

    def revalidate(
        self, current: DecisionObjectRecord, trigger_reason: str
    ) -> tuple[DecisionObjectRecord, CommitResult]:
        with self._ledger.transaction():
            profiles = self._profiles(current.profile_refs)
            required = tuple(
                dict.fromkeys(field for profile in profiles for field in profile.required_fields)
            )
            present = {
                "purpose_statement": bool(current.purpose_statement.strip()),
                "problem_frame": bool(current.problem_frame.strip()),
                "focus_refs": bool(current.focus_refs),
                "workstream_refs": bool(current.workstream_refs),
            }
            blockers = tuple(
                f"MISSING:{field}" for field in required if not present.get(field, False)
            )
            attention = "USER_INPUT_REQUIRED" if blockers else "NONE"
            entry_status = "FAIL" if blockers else "PASS"
            revised, commit = self.revise(
                current,
                updates={
                    "blockers": blockers,
                    "attention": attention,
                    "entry_criteria": {
                        "status": entry_status,
                        "reason": trigger_reason,
                    },
                    "freshness": "CURRENT",
                },
                event_type="object/revalidated",
            )
            if blockers:
                self.raise_attention(
                    revised,
                    attention_type="USER_INPUT_REQUIRED",
                    risk="MEDIUM",
                    reason="Object profile-required fields are missing",
                    condition_ref=blockers[0],
                )
            return revised, commit

    def add_relation(
        self,
        current: DecisionObjectRecord,
        *,
        relation_type: str,
        target_ref: str,
        semantic_role: str,
        evidence_refs: tuple[str, ...],
        authority_state: str,
        actor_ref: str,
    ) -> tuple[ObjectRelationRecord, DecisionObjectRecord, CommitResult]:
        with self._ledger.transaction():
            self._validate_evidence(current.project_id, evidence_refs)
            allowed = {
                item
                for profile in self._profiles(current.profile_refs)
                for item in profile.allowed_relation_types
            } or set(ALLOWED_RELATIONS)
            if relation_type not in allowed:
                raise ValueError("relation type is not allowed by the active ObjectProfile")
            now = self._clock.now()
            relation_draft: dict[str, object] = {
                "relation_revision_id": self._ids.new("object-relation-revision"),
                "relation_id": self._ids.new("object-relation"),
                "project_id": current.project_id,
                "source_object_id": current.object_id,
                "relation_type": relation_type,
                "target_ref": target_ref,
                "semantic_role": semantic_role,
                "evidence_refs": evidence_refs,
                "actor_or_agent_ref": actor_ref,
                "valid_from": now,
                "authority_state": authority_state,
                "active": True,
                "created_at": now,
            }
            relation = ObjectRelationRecord.model_validate(
                {**relation_draft, "revision_digest": self._relation_digest(relation_draft)}
            )
            self._store.add_relation(relation)
            self._dependencies.add(
                DependencyRelation(
                    relation_id=relation.relation_revision_id,
                    project_id=current.project_id,
                    source_ref=f"OBJECT:{current.object_id}",
                    relation_type=relation_type,
                    target_ref=target_ref,
                    payload={
                        "semantic_role": semantic_role,
                        "authority_state": authority_state,
                    },
                    revision_digest=relation.revision_digest,
                )
            )
            revised, commit = self.revise(
                current,
                updates={"relation_refs": (*current.relation_refs, relation.relation_id)},
                event_type="object/relationChanged",
                evidence_refs=evidence_refs,
            )
            return relation, revised, commit

    def end_relation(
        self,
        current: DecisionObjectRecord,
        relation: ObjectRelationRecord,
        *,
        reason: str,
        evidence_refs: tuple[str, ...],
    ) -> tuple[ObjectRelationRecord, DecisionObjectRecord, CommitResult]:
        with self._ledger.transaction():
            if not relation.active:
                raise ValueError("relation is already inactive")
            draft = relation.model_dump(mode="python")
            draft.update(
                {
                    "relation_revision_id": self._ids.new("object-relation-revision"),
                    "valid_to": self._clock.now(),
                    "active": False,
                    "supersedes_revision_digest": relation.revision_digest,
                    "created_at": self._clock.now(),
                }
            )
            draft.pop("revision_digest", None)
            ended = ObjectRelationRecord.model_validate(
                {**draft, "revision_digest": self._relation_digest(draft)}
            )
            self._store.add_relation(ended)
            revised, commit = self.revise(
                current,
                updates={"relation_refs": current.relation_refs},
                event_type="object/relationChanged",
                evidence_refs=evidence_refs,
            )
            self.audit(revised, "object/relationChanged", {"reason": reason})
            return ended, revised, commit

    def raise_attention(
        self,
        current: DecisionObjectRecord,
        *,
        attention_type: str,
        risk: str,
        reason: str,
        condition_ref: str,
    ) -> ObjectAttentionRecord:
        with self._ledger.transaction():
            existing = next(
                (
                    item
                    for item in self._store.list_attention(current.project_id, current.object_id)
                    if item.state != "CLEARED" and item.condition_ref == condition_ref
                ),
                None,
            )
            if existing is not None:
                return existing
            draft: dict[str, object] = {
                "attention_revision_id": self._ids.new("object-attention-revision"),
                "attention_id": self._ids.new("object-attention"),
                "project_id": current.project_id,
                "object_id": current.object_id,
                "attention_type": attention_type,
                "risk": risk,
                "reason": reason,
                "condition_ref": condition_ref,
                "state": "OPEN",
                "created_at": self._clock.now(),
            }
            record = ObjectAttentionRecord.model_validate(
                {**draft, "revision_digest": self._attention_digest(draft)}
            )
            self._store.add_attention(record)
            self.audit(current, "object/attentionRaised", {"attention_id": record.attention_id})
            return record

    def acknowledge_attention(
        self,
        current: DecisionObjectRecord,
        attention: ObjectAttentionRecord,
        *,
        actor_ref: str,
        note: str | None,
    ) -> tuple[ObjectAttentionRecord, DecisionObjectRecord, CommitResult]:
        with self._ledger.transaction():
            resolved = attention.condition_ref not in current.blockers
            draft = attention.model_dump(mode="python")
            draft.update(
                {
                    "attention_revision_id": self._ids.new("object-attention-revision"),
                    "state": "CLEARED" if resolved else "ACKNOWLEDGED",
                    "acknowledged_by": actor_ref,
                    "acknowledgement_note": note,
                    "supersedes_revision_digest": attention.revision_digest,
                    "created_at": self._clock.now(),
                }
            )
            draft.pop("revision_digest", None)
            revised_attention = ObjectAttentionRecord.model_validate(
                {**draft, "revision_digest": self._attention_digest(draft)}
            )
            self._store.add_attention(revised_attention)
            open_items = tuple(
                item
                for item in self._store.list_attention(current.project_id, current.object_id)
                if item.attention_id != attention.attention_id and item.state != "CLEARED"
            )
            object_attention = (
                open_items[0].attention_type
                if open_items
                else "NONE"
                if resolved
                else current.attention
            )
            revised, commit = self.revise(
                current,
                updates={"attention": object_attention},
                event_type=("object/attentionCleared" if resolved else "object/updated"),
            )
            return revised_attention, revised, commit

    def audit(
        self, current: DecisionObjectRecord, event_type: str, payload: dict[str, object]
    ) -> ObjectAuditRecord:
        created_at = self._clock.now()
        draft = {
            "project_id": current.project_id,
            "object_id": current.object_id,
            "event_type": event_type,
            "payload": payload,
            "created_at": created_at,
        }
        record = ObjectAuditRecord(
            audit_id=self._ids.new("object-audit"),
            project_id=current.project_id,
            object_id=current.object_id,
            event_type=event_type,
            payload=payload,
            event_digest=domain_digest("OBJECT_AUDIT", "1.0.0", canonical_payload(draft)),
            created_at=created_at,
        )
        self._store.append_audit(record)
        return record

    def _persist(
        self,
        record: DecisionObjectRecord,
        event_type: str,
        *,
        evidence_refs: tuple[str, ...] = (),
    ) -> tuple[DecisionObjectRecord, CommitResult]:
        with self._ledger.transaction():
            content = record.model_dump(mode="python")
            snapshot = EntitySnapshot(
                snapshot_id=self._ids.new("snapshot"),
                project_id=record.project_id,
                entity_type=EntityType.DECISION_OBJECT,
                entity_id=record.object_id,
                schema_version="1.0.0",
                content=content,
                content_digest=domain_digest(
                    "ENTITY_SNAPSHOT", "1.0.0", canonical_payload(content)
                ),
            )
            authenticated = current_authenticated_actor()
            actor = ActorRef(
                actor_id=record.actor_or_agent_ref,
                kind=(
                    ActorKind.AGENT
                    if record.actor_or_agent_ref.startswith("agent:")
                    else ActorKind.HUMAN
                ),
                role=(
                    authenticated.role
                    if authenticated is not None
                    and authenticated.actor_id == record.actor_or_agent_ref
                    and authenticated.project_id == record.project_id
                    else "object-editor"
                ),
                project_id=record.project_id,
                session_id=(
                    authenticated.session_id
                    if authenticated is not None
                    and authenticated.actor_id == record.actor_or_agent_ref
                    else None
                ),
                role_assignment_ref=(
                    authenticated.role_assignment_id
                    if authenticated is not None
                    and authenticated.actor_id == record.actor_or_agent_ref
                    else None
                ),
            )
            revision = SemanticRevision(
                revision_id=record.object_revision_id,
                project_id=record.project_id,
                entity_type=EntityType.DECISION_OBJECT,
                entity_id=record.object_id,
                snapshot_id=snapshot.snapshot_id,
                parent_revision_digests=(
                    ()
                    if record.supersedes_revision_digest is None
                    else (record.supersedes_revision_digest,)
                ),
                actor=actor,
                reason=event_type,
                evidence_refs=evidence_refs or record.trigger_evidence_refs,
                affected_refs=record.relation_refs,
                revision_digest=record.revision_digest,
                created_at=record.created_at,
            )
            expected = (
                {}
                if record.supersedes_revision_digest is None
                else {f"DECISION_OBJECT:{record.object_id}": record.supersedes_revision_digest}
            )
            commit = self._commits.commit(
                RevisionChangeSet(
                    changeset_id=self._ids.new("changeset"),
                    project_id=record.project_id,
                    expected_heads=expected,
                    staged_revisions=(StagedRevision(snapshot=snapshot, revision=revision),),
                    impact_plan=ImpactPropagationPlan(),
                    actor=actor,
                    reason=event_type,
                )
            )
            if not commit.committed_revision_ids:
                raise ValueError("object commit branched because expected revision changed")
            self._store.add_object(record)
            self.audit(record, event_type, {"revision_digest": record.revision_digest})
            return record, commit

    def _profiles(self, profile_refs: tuple[str, ...]) -> tuple[ObjectProfileRecord, ...]:
        profiles: list[ObjectProfileRecord] = []
        for profile_ref in profile_refs:
            profile = self._store.read_profile(profile_ref, None)
            if profile is None or not profile.enabled:
                raise ValueError(f"ObjectProfile is unavailable: {profile_ref}")
            profiles.append(profile)
        return tuple(profiles)

    def _validate_evidence(self, project_id: str, refs: tuple[str, ...]) -> None:
        for reference in refs:
            span = self._artifacts.read_evidence(reference)
            if span is None or span.project_id != project_id:
                raise ValueError("Object evidence ref is not an in-project evidence span")

    @staticmethod
    def _digest(value: dict[str, object]) -> str:
        return domain_digest("DECISION_OBJECT", "2.0.0", canonical_payload(value))

    @staticmethod
    def _relation_digest(value: dict[str, object]) -> str:
        return domain_digest("OBJECT_RELATION", "1.0.0", canonical_payload(value))

    @staticmethod
    def _attention_digest(value: dict[str, object]) -> str:
        return domain_digest("OBJECT_ATTENTION", "1.0.0", canonical_payload(value))
