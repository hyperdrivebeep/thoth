from __future__ import annotations

from thoth.application.services.baseline_router import BaselineRouter
from thoth.domain.auth import require_authenticated_authority
from thoth.domain.baseline import (
    BaselineCandidate,
    BaselineDecision,
    BaselineSet,
    MultiBaselineProjection,
    ProjectHeadSet,
)
from thoth.domain.canonical import canonical_payload, domain_digest, head_set_digest
from thoth.ports.baseline import BaselineStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


class BaselineService:
    def __init__(
        self,
        *,
        store: BaselineStorePort,
        ledger: LedgerPort,
        router: BaselineRouter,
        clock: ClockPort,
        ids: IdGeneratorPort,
        policy_version: str = "baseline:1.0.0",
    ) -> None:
        self._store = store
        self._ledger = ledger
        self._router = router
        self._clock = clock
        self._ids = ids
        self._policy_version = policy_version

    def refresh(
        self,
        *,
        project_id: str,
        thread_id: str | None,
        purpose: str,
        propose_candidates: bool = True,
    ) -> MultiBaselineProjection:
        heads = dict(self._ledger.read_heads(project_id))
        previous = self._store.read_head_set(project_id)
        head_set = ProjectHeadSet(
            project_id=project_id,
            head_map=heads,
            head_set_digest=head_set_digest(heads),
            revision=1 if previous is None else previous.revision + 1,
            generated_at=self._clock.now(),
        )
        self._store.put_head_set(head_set)
        sets = self._store.list_sets(project_id)
        stale_ids = tuple(
            item.baseline_set_id
            for item in sets
            if item.lifecycle == "CURRENT"
            and any(heads.get(key) != digest for key, digest in item.head_map.items())
        )
        self._store.mark_sets_stale(project_id, stale_ids)
        existing = self._store.list_candidates(project_id)
        created: list[BaselineCandidate] = []
        if propose_candidates:
            for scope, scoped_heads in self._router.route(heads).items():
                duplicate = next(
                    (
                        item
                        for item in existing
                        if item.scope == scope
                        and item.head_map == scoped_heads
                        and item.state == "PENDING_PROTECTED_DECISION"
                    ),
                    None,
                )
                if duplicate is not None:
                    created.append(duplicate)
                    continue
                now = self._clock.now()
                candidate_id = self._ids.new("baseline-candidate")
                draft: dict[str, object] = {
                    "candidate_id": candidate_id,
                    "project_id": project_id,
                    "thread_id": thread_id,
                    "scope": scope.value,
                    "purpose": purpose,
                    "head_map": scoped_heads,
                    "project_head_set_digest": head_set.head_set_digest,
                    "policy_version": self._policy_version,
                    "evidence_refs": (),
                    "state": "PENDING_PROTECTED_DECISION",
                    "protected": True,
                    "created_at": now,
                }
                candidate = BaselineCandidate.model_validate(
                    {
                        **draft,
                        "candidate_digest": domain_digest(
                            "BASELINE_CANDIDATE",
                            "1.0.0",
                            canonical_payload(draft),
                        ),
                    }
                )
                self._store.add_candidate(candidate)
                created.append(candidate)
        current_sets = tuple(
            item for item in self._store.list_sets(project_id) if item.lifecycle == "CURRENT"
        )
        return MultiBaselineProjection(
            project_head_set=head_set,
            baseline_candidates=tuple(created),
            current_baseline_sets=current_sets,
            stale_baseline_set_ids=stale_ids,
        )

    def decide(
        self,
        *,
        project_id: str,
        candidate_id: str,
        decision: str,
        actor_ref: str,
        role_assignment_ref: str,
        approved_digest: str,
    ) -> tuple[BaselineDecision, BaselineSet | None]:
        require_authenticated_authority(project_id, actor_ref, role_assignment_ref)
        candidate = self._store.read_candidate(project_id, candidate_id)
        if candidate is None:
            raise ValueError("typed baseline candidate not found")
        if candidate.candidate_digest != approved_digest:
            raise ValueError("baseline candidate digest mismatch")
        if candidate.state != "PENDING_PROTECTED_DECISION":
            raise ValueError("baseline candidate is no longer pending")
        heads = self._ledger.read_heads(project_id)
        if any(heads.get(key) != digest for key, digest in candidate.head_map.items()):
            raise ValueError("baseline candidate is stale against Working Heads")
        now = self._clock.now()
        current_same_scope = tuple(
            item
            for item in self._store.list_sets(project_id)
            if item.scope == candidate.scope and item.lifecycle == "CURRENT"
        )
        baseline: BaselineSet | None = None
        if decision == "APPROVE":
            baseline_id = self._ids.new("baseline-set")
            baseline_draft: dict[str, object] = {
                "baseline_set_id": baseline_id,
                "project_id": project_id,
                "scope": candidate.scope.value,
                "purpose": candidate.purpose,
                "head_map": candidate.head_map,
                "source_candidate_id": candidate.candidate_id,
                "policy_version": candidate.policy_version,
                "lifecycle": "CURRENT",
                "supersedes_baseline_set_id": (
                    None if not current_same_scope else current_same_scope[-1].baseline_set_id
                ),
                "authority_actor_ref": actor_ref,
                "authority_role_assignment_ref": role_assignment_ref,
                "recalculation_required": False,
                "created_at": now,
            }
            baseline = BaselineSet.model_validate(
                {
                    **baseline_draft,
                    "baseline_set_digest": domain_digest(
                        "BASELINE_SET", "1.0.0", canonical_payload(baseline_draft)
                    ),
                }
            )
        decision_id = self._ids.new("baseline-decision")
        decision_draft: dict[str, object] = {
            "decision_id": decision_id,
            "project_id": project_id,
            "candidate_id": candidate.candidate_id,
            "decision": decision,
            "actor_ref": actor_ref,
            "role_assignment_ref": role_assignment_ref,
            "approved_digest": approved_digest,
            "resulting_baseline_set_id": (None if baseline is None else baseline.baseline_set_id),
            "protected": True,
            "decided_at": now,
        }
        result = BaselineDecision.model_validate(
            {
                **decision_draft,
                "decision_digest": domain_digest(
                    "BASELINE_DECISION", "1.0.0", canonical_payload(decision_draft)
                ),
            }
        )
        self._store.commit_decision(
            candidate,
            result,
            baseline,
            tuple(item.baseline_set_id for item in current_same_scope),
        )
        return result, baseline

    def list_candidates(self, project_id: str) -> tuple[BaselineCandidate, ...]:
        return self._store.list_candidates(project_id)

    def list_sets(self, project_id: str) -> tuple[BaselineSet, ...]:
        return self._store.list_sets(project_id)

    def read_set(self, project_id: str, digest: str) -> BaselineSet | None:
        return self._store.read_set_by_digest(project_id, digest)

    def require_current_comparator(self, project_id: str, digest: str) -> BaselineSet:
        value = self.read_set(project_id, digest)
        if value is None or value.lifecycle != "CURRENT":
            raise ValueError("Outcome comparator requires a current typed BaselineSet")
        return value

    def composite_current_digest(self, project_id: str) -> str | None:
        current = tuple(item for item in self.list_sets(project_id) if item.lifecycle == "CURRENT")
        if not current:
            return None
        return domain_digest(
            "COMPOSITE_BASELINE_SET",
            "1.0.0",
            canonical_payload(
                {
                    "project_id": project_id,
                    "members": tuple(
                        (item.scope.value, item.baseline_set_digest)
                        for item in sorted(current, key=lambda child: child.scope.value)
                    ),
                }
            ),
        )
