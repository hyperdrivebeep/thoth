from __future__ import annotations

from typing import cast

from thoth.application.services.revision_diff import semantic_diff
from thoth.application.services.revision_service import RevisionCommitService
from thoth.domain.actor import ActorRef
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ActorKind
from thoth.domain.revision import (
    EntitySnapshot,
    ImpactPropagationPlan,
    RevisionChangeSet,
    SemanticDiffEntry,
    SemanticMergeGates,
    SemanticMergeResult,
    SemanticMergeState,
    SemanticRevision,
    StagedRevision,
)
from thoth.ports.dependency import DependencyGraphPort
from thoth.ports.ledger import LedgerPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort

_PROTECTED = {
    "authority": "AUTHORITY_REVIEW_REQUIRED",
    "approval": "AUTHORITY_REVIEW_REQUIRED",
    "cutoff": "CUTOFF_REVIEW_REQUIRED",
    "policy": "POLICY_REVIEW_REQUIRED",
    "security": "POLICY_REVIEW_REQUIRED",
    "dependency": "DEPENDENCY_REVIEW_REQUIRED",
    "criterion": "DOMAIN_REVIEW_REQUIRED",
    "disposition": "DOMAIN_REVIEW_REQUIRED",
}
_ACTION_PROTECTED = {
    "execution_authority": "AUTHORITY_REVIEW_REQUIRED",
    "required_approver_role": "AUTHORITY_REVIEW_REQUIRED",
    "required_roles": "AUTHORITY_REVIEW_REQUIRED",
    "required_role_union": "AUTHORITY_REVIEW_REQUIRED",
    "authorization_state": "AUTHORITY_REVIEW_REQUIRED",
    "authorization_refs": "AUTHORITY_REVIEW_REQUIRED",
    "risk_tier": "POLICY_REVIEW_REQUIRED",
    "effect_facts": "POLICY_REVIEW_REQUIRED",
    "effect_vector": "POLICY_REVIEW_REQUIRED",
    "effect_completeness_confirmed": "POLICY_REVIEW_REQUIRED",
    "external_write": "POLICY_REVIEW_REQUIRED",
    "physical_action": "POLICY_REVIEW_REQUIRED",
    "sandbox_required": "POLICY_REVIEW_REQUIRED",
    "required_processes": "POLICY_REVIEW_REQUIRED",
    "required_process_union": "POLICY_REVIEW_REQUIRED",
}
_MISSING = object()


class SemanticThreeWayMergeService:
    def __init__(
        self,
        *,
        ledger: LedgerPort,
        dependencies: DependencyGraphPort,
        commits: RevisionCommitService,
        clock: ClockPort,
        ids: IdGeneratorPort,
    ) -> None:
        self._ledger = ledger
        self._dependencies = dependencies
        self._commits = commits
        self._clock = clock
        self._ids = ids

    def merge(
        self,
        *,
        project_id: str,
        entity_id: str,
        left_digest: str,
        right_digest: str,
        merge_policy_ref: str,
        reason: str,
        evidence_refs: tuple[str, ...],
    ) -> SemanticMergeResult:
        left = self._ledger.read_revision_by_digest(project_id, left_digest)
        right = self._ledger.read_revision_by_digest(project_id, right_digest)
        if (
            left is None
            or right is None
            or left.entity_id != entity_id
            or right.entity_id != entity_id
            or left.entity_type != right.entity_type
        ):
            raise ValueError("merge parents must be revisions of the same project aggregate")
        ancestor_digest = self._common_ancestor(project_id, left_digest, right_digest)
        ancestor = (
            None
            if ancestor_digest is None
            else self._ledger.read_revision_by_digest(project_id, ancestor_digest)
        )
        left_snapshot = self._ledger.read_snapshot(left.snapshot_id)
        right_snapshot = self._ledger.read_snapshot(right.snapshot_id)
        ancestor_snapshot = (
            None if ancestor is None else self._ledger.read_snapshot(ancestor.snapshot_id)
        )
        if left_snapshot is None or right_snapshot is None:
            raise ValueError("merge parent snapshot is missing")
        left_changes = semantic_diff(
            {} if ancestor_snapshot is None else ancestor_snapshot.content,
            left_snapshot.content,
        )
        right_changes = semantic_diff(
            {} if ancestor_snapshot is None else ancestor_snapshot.content,
            right_snapshot.content,
        )
        left_paths = tuple(item.path for item in left_changes)
        right_paths = tuple(item.path for item in right_changes)
        reason_codes: list[str] = []
        conflicts = set(left_paths).intersection(right_paths)
        if conflicts:
            reason_codes.append("OVERLAPPING_FIELD_CHANGE")
        sibling_branches = ancestor_digest not in {None, left_digest, right_digest}
        if not sibling_branches:
            reason_codes.append("SAME_PARENT_BRANCH_REQUIRED")
        protected = self._protected_changes((*left_changes, *right_changes))
        conflicts.update(protected)
        reason_codes.extend(protected.values())
        schema_ok = (
            ancestor_snapshot is not None
            and ancestor_snapshot.schema_version == left_snapshot.schema_version
            and ancestor_snapshot.schema_version == right_snapshot.schema_version
        )
        if not schema_ok:
            reason_codes.append("SCHEMA_MISMATCH")
        domain_conflicts = self._domain_type_conflicts(
            {} if ancestor_snapshot is None else ancestor_snapshot.content,
            left_snapshot.content,
            right_snapshot.content,
            frozenset((*left_paths, *right_paths)),
        )
        conflicts.update(domain_conflicts)
        if domain_conflicts:
            reason_codes.append("DOMAIN_TYPE_MISMATCH")
        current_head = self._ledger.read_heads(project_id).get(
            f"{left.entity_type.value}:{entity_id}"
        )
        head_ok = current_head in {left_digest, right_digest}
        if not head_ok:
            reason_codes.append("STALE_MERGE_HEAD")
        gates = SemanticMergeGates(
            common_ancestor=sibling_branches,
            schema_compatible=schema_ok,
            authority=not any(code == "AUTHORITY_REVIEW_REQUIRED" for code in protected.values()),
            cutoff=not any(code == "CUTOFF_REVIEW_REQUIRED" for code in protected.values()),
            policy=not any(code == "POLICY_REVIEW_REQUIRED" for code in protected.values()),
            dependency=not any(code == "DEPENDENCY_REVIEW_REQUIRED" for code in protected.values()),
            domain=not domain_conflicts
            and not any(code == "DOMAIN_REVIEW_REQUIRED" for code in protected.values()),
            current_head=head_ok,
        )
        merge_id = self._ids.new("semantic-merge")
        if conflicts or not all(gates.model_dump(mode="python").values()):
            return SemanticMergeResult(
                merge_id=merge_id,
                project_id=project_id,
                entity_type=left.entity_type,
                entity_id=entity_id,
                state=SemanticMergeState.OPEN_CONFLICT,
                common_ancestor_digest=ancestor_digest,
                left_revision_digest=left_digest,
                right_revision_digest=right_digest,
                left_changed_paths=left_paths,
                right_changed_paths=right_paths,
                conflict_paths=tuple(sorted(conflicts)),
                reason_codes=tuple(dict.fromkeys(reason_codes or ["OVERLAPPING_FIELD_CHANGE"])),
                gates=gates,
                head_mutated=False,
            )
        assert ancestor_snapshot is not None
        merged_content = self._merge_value(
            ancestor_snapshot.content,
            left_snapshot.content,
            right_snapshot.content,
        )
        merged_content = cast(dict[str, object], merged_content)
        snapshot = EntitySnapshot(
            snapshot_id=self._ids.new("snapshot"),
            project_id=project_id,
            entity_type=left.entity_type,
            entity_id=entity_id,
            schema_version=left_snapshot.schema_version,
            content=merged_content,
            content_digest=domain_digest(
                "ENTITY_SNAPSHOT", "1.0.0", canonical_payload(merged_content)
            ),
        )
        now = self._clock.now()
        authenticated = current_authenticated_actor()
        actor = ActorRef(
            actor_id=(
                "agent:semantic-merge-coordinator"
                if authenticated is None
                else authenticated.actor_id
            ),
            kind=ActorKind.AGENT if authenticated is None else ActorKind.HUMAN,
            role=("semantic-merge-coordinator" if authenticated is None else authenticated.role),
            project_id=project_id,
            session_id=None if authenticated is None else authenticated.session_id,
            role_assignment_ref=(
                None if authenticated is None else authenticated.role_assignment_id
            ),
        )
        revision_id = self._ids.new("revision")
        revision_draft: dict[str, object] = {
            "revision_id": revision_id,
            "project_id": project_id,
            "entity_type": left.entity_type.value,
            "entity_id": entity_id,
            "snapshot_id": snapshot.snapshot_id,
            "parent_revision_digests": (left_digest, right_digest),
            "actor": actor,
            "reason": reason,
            "evidence_refs": tuple(
                dict.fromkeys((*left.evidence_refs, *right.evidence_refs, *evidence_refs))
            ),
            "affected_refs": tuple(sorted(set(left_paths).union(right_paths))),
            "created_at": now,
            "schema_version": "1.0.0",
        }
        revision = SemanticRevision.model_validate(
            {
                **revision_draft,
                "revision_digest": domain_digest(
                    "SEMANTIC_REVISION", "1.0.0", canonical_payload(revision_draft)
                ),
            }
        )
        aggregate_key = f"{left.entity_type.value}:{entity_id}"
        dependents = self._dependencies.downstream(project_id, aggregate_key)
        commit = self._commits.commit(
            RevisionChangeSet(
                changeset_id=merge_id,
                project_id=project_id,
                expected_heads={aggregate_key: str(current_head)},
                staged_revisions=(StagedRevision(snapshot=snapshot, revision=revision),),
                impact_plan=ImpactPropagationPlan(recalculate_refs=dependents),
                actor=actor,
                reason=f"{reason} [{merge_policy_ref}]",
            )
        )
        if not commit.committed_revision_ids:
            raise ValueError("merge head changed during atomic commit")
        return SemanticMergeResult(
            merge_id=merge_id,
            project_id=project_id,
            entity_type=left.entity_type,
            entity_id=entity_id,
            state=SemanticMergeState.AUTO_MERGED,
            common_ancestor_digest=ancestor_digest,
            left_revision_digest=left_digest,
            right_revision_digest=right_digest,
            left_changed_paths=left_paths,
            right_changed_paths=right_paths,
            conflict_paths=(),
            reason_codes=(),
            gates=gates,
            merged_revision_digest=revision.revision_digest,
            receipt_ref=commit.receipt.receipt_id,
            head_mutated=True,
        )

    def _common_ancestor(self, project_id: str, left: str, right: str) -> str | None:
        left_ancestors = self._ancestors(project_id, left)
        right_ancestors = self._ancestors(project_id, right)
        shared = set(left_ancestors).intersection(right_ancestors)
        return (
            None
            if not shared
            else min(shared, key=lambda item: left_ancestors[item] + right_ancestors[item])
        )

    def _ancestors(self, project_id: str, root: str) -> dict[str, int]:
        values: dict[str, int] = {}
        frontier = [(root, 0)]
        while frontier:
            digest, depth = frontier.pop(0)
            if digest in values and values[digest] <= depth:
                continue
            values[digest] = depth
            revision = self._ledger.read_revision_by_digest(project_id, digest)
            if revision is not None:
                frontier.extend((parent, depth + 1) for parent in revision.parent_revision_digests)
        return values

    @classmethod
    def _protected_changes(
        cls,
        changes: tuple[SemanticDiffEntry, ...],
    ) -> dict[str, str]:
        protected: dict[str, str] = {}
        for change in changes:
            reason = cls._protected_reason(change)
            if reason is not None:
                protected[change.path] = reason
        return protected

    @classmethod
    def _protected_reason(
        cls,
        change: SemanticDiffEntry,
    ) -> str | None:
        segments = tuple(cls._unescape(part).casefold() for part in change.path.split("/") if part)
        direct = cls._segment_reason(segments)
        if direct is not None:
            return direct
        before = change.before if change.before_present else _MISSING
        after = change.after if change.after_present else _MISSING
        return cls._changed_value_reason(before, after)

    @staticmethod
    def _unescape(value: str) -> str:
        return value.replace("~1", "/").replace("~0", "~")

    @staticmethod
    def _segment_reason(segments: tuple[str, ...]) -> str | None:
        for segment in segments:
            exact = _ACTION_PROTECTED.get(segment)
            if exact is not None:
                return exact
            general = next(
                (code for token, code in _PROTECTED.items() if token in segment),
                None,
            )
            if general is not None:
                return general
        return None

    @classmethod
    def _changed_value_reason(cls, before: object, after: object) -> str | None:
        if before == after:
            return None
        if isinstance(before, dict) or isinstance(after, dict):
            left = cast(dict[object, object], before) if isinstance(before, dict) else {}
            right = cast(dict[object, object], after) if isinstance(after, dict) else {}
            for raw_key in set(left) | set(right):
                left_value = left.get(raw_key, _MISSING)
                right_value = right.get(raw_key, _MISSING)
                if left_value == right_value:
                    continue
                reason = cls._segment_reason((str(raw_key).casefold(),))
                reason = reason or cls._changed_value_reason(left_value, right_value)
                if reason is not None:
                    return reason
        elif isinstance(before, list | tuple) or isinstance(after, list | tuple):
            left_items: tuple[object, ...] = (
                tuple(cast(list[object] | tuple[object, ...], before))
                if isinstance(before, list | tuple)
                else ()
            )
            right_items: tuple[object, ...] = (
                tuple(cast(list[object] | tuple[object, ...], after))
                if isinstance(after, list | tuple)
                else ()
            )
            for index in range(max(len(left_items), len(right_items))):
                left_value = left_items[index] if index < len(left_items) else _MISSING
                right_value = right_items[index] if index < len(right_items) else _MISSING
                reason = cls._changed_value_reason(left_value, right_value)
                if reason is not None:
                    return reason
        return None

    @staticmethod
    def _domain_type_conflicts(
        base: dict[str, object],
        left: dict[str, object],
        right: dict[str, object],
        paths: frozenset[str],
    ) -> tuple[str, ...]:
        conflicts: list[str] = []
        for path in paths:
            key = path.removeprefix("/")
            if "/" in key or key not in base:
                continue
            expected = type(base[key])
            for content in (left, right):
                if key in content and type(content[key]) is not expected:
                    conflicts.append(path)
        return tuple(sorted(set(conflicts)))

    @classmethod
    def _merge_value(cls, base: object, left: object, right: object) -> object:
        if left == right:
            return left
        if left == base:
            return right
        if right == base:
            return left
        if isinstance(base, dict) and isinstance(left, dict) and isinstance(right, dict):
            base_values = cast(dict[str, object], base)
            left_values = cast(dict[str, object], left)
            right_values = cast(dict[str, object], right)
            result: dict[str, object] = {}
            for key in sorted(set(base_values).union(left_values, right_values)):
                if key not in left_values:
                    result[key] = right_values[key]
                elif key not in right_values or key not in base_values:
                    result[key] = left_values[key]
                else:
                    result[key] = cls._merge_value(
                        base_values[key],
                        left_values[key],
                        right_values[key],
                    )
            return result
        raise ValueError("overlapping semantic change reached auto-merge reducer")
