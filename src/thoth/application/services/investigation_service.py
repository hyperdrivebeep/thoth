from __future__ import annotations

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.investigation import InvestigationAuditRecord, InvestigationRecord
from thoth.domain.project import WorkThread
from thoth.ports.investigation import InvestigationStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort

TERMINAL_STATE_BY_STOP_REASON = {
    "SUFFICIENT": "CONVERGED",
    "SEARCH_SATURATED": "CONVERGED",
    "DIMINISHING_INFORMATION_VALUE": "CONVERGED",
    "PERMISSION_BLOCKED": "HOLD",
    "EGRESS_BLOCKED": "HOLD",
    "HIGH_RISK_CONFLICT": "HOLD",
    "DEPTH_LIMIT": "ABSTAINED",
    "BUDGET_EXHAUSTED": "ABSTAINED",
    "TIME_LIMIT": "ABSTAINED",
    "USER_STOPPED": "STOPPED",
    "PARENT_THREAD_STOPPED": "STOPPED",
    "CONNECTOR_FAILED": "ABSTAINED",
    "EVIDENCE_COMMIT_FAILED": "ABSTAINED",
}


class InvestigationService:
    def __init__(
        self,
        *,
        store: InvestigationStorePort,
        clock: ClockPort,
        ids: IdGeneratorPort,
    ) -> None:
        self._store = store
        self._clock = clock
        self._ids = ids

    def start(
        self,
        *,
        thread: WorkThread,
        trigger: str,
        question: str,
        parent_investigation_id: str | None = None,
        target_object_id: str | None = None,
        target_hypothesis_id: str | None = None,
        mode: str = "BOUNDED",
        scope: dict[str, str] | None = None,
        required_evidence_groups: tuple[str, ...] = (),
        query_families: tuple[str, ...] = (),
        budget: int = 30,
        stop_conditions: tuple[str, ...] = (),
        explicit_saturation_opt_in: bool = False,
    ) -> InvestigationRecord:
        conditions = list(stop_conditions)
        if explicit_saturation_opt_in:
            conditions.append("EXPLICIT_SATURATION_OPT_IN")
        now = self._clock.now()
        draft: dict[str, object] = {
            "investigation_id": self._ids.new("investigation"),
            "project_id": thread.project_id,
            "thread_id": thread.thread_id,
            "cycle_id": thread.cycle_id,
            "parent_investigation_id": parent_investigation_id,
            "trigger": trigger,
            "question": question,
            "target_object_id": target_object_id,
            "target_hypothesis_id": target_hypothesis_id,
            "mode": mode,
            "scope": scope or thread.scope,
            "required_evidence_groups": required_evidence_groups,
            "query_families": query_families,
            "counter_search_policy": "REQUIRED_BEFORE_CONCLUSION",
            "budget": budget,
            "budget_usage": 0,
            "stop_conditions": tuple(dict.fromkeys(conditions)),
            "domain_state": "ACTIVE",
            "execution_state": "IDLE",
            "current_wave": 0,
            "observation_count": 0,
            "open_lead_count": 0,
            "claim_candidate_count": 0,
            "gap_count": 0,
            "sufficiency": {},
            "plan_revision": 0,
            "created_at": now,
            "updated_at": now,
        }
        value = InvestigationRecord.model_validate(
            {
                **draft,
                "investigation_digest": self._digest(draft),
            }
        )
        self._store.create(value)
        self.audit(value, "investigation/started", {"trigger": trigger, "mode": mode})
        return value

    def update_plan(
        self,
        value: InvestigationRecord,
        *,
        expected_plan_revision: int,
        mode: str | None,
        scope: dict[str, str] | None,
        required_evidence_groups: tuple[str, ...] | None,
        query_families: tuple[str, ...] | None,
        budget_extension: int,
        stop_conditions: tuple[str, ...] | None,
        explicit_saturation_opt_in: bool,
        reason: str,
    ) -> InvestigationRecord:
        if value.plan_revision != expected_plan_revision:
            raise ValueError("investigation plan revision changed")
        if value.domain_state in {"CONVERGED", "HOLD", "ABSTAINED", "STOPPED"}:
            raise ValueError("terminal investigation is immutable")
        next_scope = value.scope if scope is None else scope
        protected_keys = {"cutoff", "access", "egress", "security"}
        if any(next_scope.get(key) != value.scope.get(key) for key in protected_keys):
            raise ValueError("investigation update cannot widen protected project scope")
        next_conditions = value.stop_conditions if stop_conditions is None else stop_conditions
        if explicit_saturation_opt_in:
            next_conditions = (*next_conditions, "EXPLICIT_SATURATION_OPT_IN")
        updates: dict[str, object] = {
            "mode": value.mode if mode is None else mode,
            "scope": next_scope,
            "required_evidence_groups": (
                value.required_evidence_groups
                if required_evidence_groups is None
                else required_evidence_groups
            ),
            "query_families": (value.query_families if query_families is None else query_families),
            "budget": value.budget + budget_extension,
            "stop_conditions": tuple(dict.fromkeys(next_conditions)),
            "plan_revision": value.plan_revision + 1,
            "updated_at": self._clock.now(),
        }
        candidate = value.model_copy(update=updates)
        candidate = candidate.model_copy(
            update={"investigation_digest": self._digest(candidate.model_dump(mode="python"))}
        )
        if not self._store.update(candidate, expected_plan_revision=expected_plan_revision):
            raise ValueError("investigation plan changed concurrently")
        self.audit(
            candidate,
            "investigation/updated",
            {"reason": reason, "plan_revision": candidate.plan_revision},
        )
        return candidate

    def pause(self, value: InvestigationRecord) -> InvestigationRecord:
        return self._execution_transition(value, "PAUSED", "USER_PAUSED")

    def record_wave(
        self,
        value: InvestigationRecord,
        *,
        observation_count: int,
        lead_count: int,
        claim_candidate_count: int,
        remaining_target_gaps: tuple[str, ...],
    ) -> InvestigationRecord:
        if value.domain_state != "ACTIVE":
            raise ValueError("terminal investigation cannot record a wave")
        if value.budget_usage >= value.budget:
            raise ValueError("investigation budget is exhausted")
        updated = value.model_copy(
            update={
                "execution_state": "IDLE",
                "current_wave": value.current_wave + 1,
                "budget_usage": value.budget_usage + 1,
                "observation_count": value.observation_count + observation_count,
                "open_lead_count": value.open_lead_count + lead_count,
                "claim_candidate_count": (
                    value.claim_candidate_count + claim_candidate_count
                ),
                "gap_count": len(remaining_target_gaps),
                "sufficiency": {
                    "remaining_target_gaps": remaining_target_gaps,
                    "target_gap_closed": not remaining_target_gaps,
                },
                "plan_revision": value.plan_revision + 1,
                "updated_at": self._clock.now(),
            }
        )
        updated = updated.model_copy(
            update={"investigation_digest": self._digest(updated.model_dump(mode="python"))}
        )
        if not self._store.update(updated, expected_plan_revision=value.plan_revision):
            raise ValueError("investigation changed concurrently")
        self.audit(
            updated,
            "investigation/waveCompleted",
            {
                "current_wave": updated.current_wave,
                "remaining_target_gaps": remaining_target_gaps,
            },
        )
        return updated

    def resume(
        self, value: InvestigationRecord, *, expected_checkpoint_digest: str
    ) -> InvestigationRecord:
        if value.checkpoint_digest != expected_checkpoint_digest:
            raise ValueError("investigation checkpoint is stale")
        return self._execution_transition(value, "IDLE", "RESUMED")

    def stop(self, value: InvestigationRecord, *, reason: str) -> InvestigationRecord:
        try:
            domain_state = TERMINAL_STATE_BY_STOP_REASON[reason]
        except KeyError as exc:
            raise ValueError("unsupported investigation stop reason") from exc
        result: dict[str, object] = {
            "stop_reason": reason,
            "searched_scope": value.scope,
            "missing_or_conflict": value.sufficiency,
            "budget_usage": value.budget_usage,
            "current_wave": value.current_wave,
        }
        updated = value.model_copy(
            update={
                "domain_state": domain_state,
                "execution_state": "IDLE",
                "result": result,
                "plan_revision": value.plan_revision + 1,
                "updated_at": self._clock.now(),
            }
        )
        updated = updated.model_copy(
            update={"investigation_digest": self._digest(updated.model_dump(mode="python"))}
        )
        if not self._store.update(updated, expected_plan_revision=value.plan_revision):
            raise ValueError("investigation changed concurrently")
        self.audit(updated, "investigation/completed", result)
        return updated

    def audit(
        self, value: InvestigationRecord, event_type: str, payload: dict[str, object]
    ) -> InvestigationAuditRecord:
        draft = {
            "project_id": value.project_id,
            "investigation_id": value.investigation_id,
            "event_type": event_type,
            "payload": payload,
            "plan_revision": value.plan_revision,
            "created_at": self._clock.now(),
        }
        record = InvestigationAuditRecord(
            audit_id=self._ids.new("investigation-audit"),
            project_id=value.project_id,
            investigation_id=value.investigation_id,
            event_type=event_type,
            payload=payload,
            event_digest=domain_digest("INVESTIGATION_AUDIT", "1.0.0", canonical_payload(draft)),
            created_at=self._clock.now(),
        )
        self._store.append_audit(record)
        return record

    def _execution_transition(
        self, value: InvestigationRecord, execution_state: str, reason: str
    ) -> InvestigationRecord:
        checkpoint_payload = {
            "investigation_id": value.investigation_id,
            "plan_revision": value.plan_revision,
            "budget_usage": value.budget_usage,
            "current_wave": value.current_wave,
            "execution_state": execution_state,
        }
        checkpoint_digest = domain_digest(
            "INVESTIGATION_CHECKPOINT",
            "1.0.0",
            canonical_payload(checkpoint_payload),
        )
        updated = value.model_copy(
            update={
                "execution_state": execution_state,
                "checkpoint_digest": checkpoint_digest,
                "plan_revision": value.plan_revision + 1,
                "updated_at": self._clock.now(),
            }
        )
        updated = updated.model_copy(
            update={"investigation_digest": self._digest(updated.model_dump(mode="python"))}
        )
        if not self._store.update(updated, expected_plan_revision=value.plan_revision):
            raise ValueError("investigation changed concurrently")
        self.audit(
            updated,
            "investigation/checkpointCreated",
            {"reason": reason, "checkpoint_digest": checkpoint_digest},
        )
        return updated

    @staticmethod
    def _digest(value: dict[str, object]) -> str:
        selected = {
            key: child
            for key, child in value.items()
            if key not in {"investigation_digest", "updated_at"}
        }
        return domain_digest("INVESTIGATION", "1.0.0", canonical_payload(selected))
