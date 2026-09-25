"""Exact-scope exposure preparation and authority; arming never claims execution."""

from datetime import timedelta
from typing import Literal, cast

from pydantic import JsonValue

from thoth.application.services.behavior_candidate_service import BehaviorCandidateService
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.improvement_thread_scope import ImprovementThreadScope
from thoth.application.services.paired_evaluation_service import PairedEvaluationService
from thoth.domain.auth import (
    authenticated_data_scope_allows,
    current_authenticated_actor,
    require_authenticated_authority,
)
from thoth.domain.behavior_execution import (
    BehaviorApproval,
    BehaviorBaselineRevision,
    BehaviorExecutionRecord,
    BehaviorExposureRecord,
    BehaviorExposureSpec,
    BehaviorStageEvidence,
    select_behavior_baseline,
)
from thoth.domain.behavior_policy import BehaviorPolicyError
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.control_record import ControlRecord
from thoth.domain.evaluation_run import PairedRunRecord, sealed_payload
from thoth.domain.resource_scope import ResourceScopeError
from thoth.ports.auth import AuthSessionStorePort
from thoth.ports.behavior_artifact import BehaviorArtifactStorePort
from thoth.ports.behavior_execution import BehaviorExecutionStorePort
from thoth.ports.behavior_resolver import BehaviorComponentRegistryPort
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.thread import ThreadStorePort


class ImprovementExposureService:
    def __init__(
        self,
        *,
        store: BehaviorExecutionStorePort,
        behaviors: BehaviorArtifactStorePort,
        candidates: BehaviorCandidateService,
        components: BehaviorComponentRegistryPort,
        records: ControlRecordStorePort,
        controls: ControlRecordService,
        paired: PairedEvaluationService | None,
        governance: GovernanceStorePort,
        sessions: AuthSessionStorePort,
        threads: ThreadStorePort,
        thread_scope: ImprovementThreadScope,
        ledger: LedgerPort,
        clock: ClockPort,
        ids: IdGeneratorPort,
        environment: str = "LOCAL",
    ) -> None:
        self._store, self._behaviors, self._candidates, self._components = (
            store,
            behaviors,
            candidates,
            components,
        )
        self._records, self._controls, self._paired = records, controls, paired
        self._governance, self._sessions, self._threads = governance, sessions, threads
        self._thread_scope, self._ledger, self._clock, self._ids = thread_scope, ledger, clock, ids
        self._environment = environment

    def _write(self, record: BehaviorExposureRecord, expected: int) -> BehaviorExposureRecord:
        with self._ledger.transaction():
            self._store.append(record, expected_revision=expected)
            self._controls.create(
                project_id=record.spec.project_id,
                namespace="IMPROVEMENT",
                record_type="CANARY_RUN" if record.spec.stage == "CANARY" else "SHADOW_RUN",
                record_id=record.spec.exposure_id,
                state=record.state,
                payload={
                    "improvement_revision_id": record.spec.improvement_ref,
                    "evaluation_plan_id": record.spec.evaluation_plan_ref,
                    "requested_exposure_state": record.spec.stage,
                    "runtime_record_digest": record.record_digest,
                    "runtime_spec": record.spec.model_dump(mode="python"),
                    "approval_target_digest": record.spec.spec_digest,
                    "authorization_required": record.spec.stage == "CANARY",
                    "stage_evidence": record.stage_evidence,
                    "actual_execution_observed": bool(record.observations),
                    "decision_exposure_observed": any(
                        item.decision_exposed for item in record.observations
                    ),
                    "output_authoritative": False,
                },
            )
        return record

    def transition(self, old: BehaviorExposureRecord, **changes: object) -> BehaviorExposureRecord:
        payload = old.model_dump(mode="python", exclude={"record_digest"})
        payload.update(changes)
        payload["revision"] = old.revision + 1
        record = BehaviorExposureRecord.model_validate(
            sealed_payload("BEHAVIOR_EXPOSURE_RECORD", "record_digest", payload)
        )
        return self._write(record, old.revision)

    def read(self, project: str, identifier: str) -> BehaviorExposureRecord:
        record = self._store.read(project, identifier)
        if record is None:
            raise BehaviorPolicyError("BEHAVIOR_EXPOSURE_NOT_FOUND")
        proposal = self._records.read(project, "IMPROVEMENT", record.spec.improvement_ref)
        if proposal is None:
            raise BehaviorPolicyError("BEHAVIOR_PROPOSAL_NOT_FOUND")
        self._thread_scope.require_record(proposal)
        return record

    @staticmethod
    def _budget(values: dict[str, JsonValue], name: str) -> int:
        value = values.get(name)
        if type(value) is not int:
            raise BehaviorPolicyError("BEHAVIOR_EXPOSURE_BUDGET_REQUIRED")
        return value

    def _target(
        self, project: str, proposal: ControlRecord, scope: dict[str, str]
    ) -> tuple[str | None, str | None]:
        if set(scope) - {"environment", "thread_id", "workstream"}:
            raise BehaviorPolicyError("BEHAVIOR_EXPOSURE_SCOPE_INVALID")
        if scope.get("environment", self._environment) != self._environment:
            raise BehaviorPolicyError("BEHAVIOR_ENVIRONMENT_MISMATCH")
        original = proposal.payload.get("thread_id")
        target = scope.get("thread_id", original if isinstance(original, str) else None)
        workstream = scope.get("workstream")
        if isinstance(original, str) and (target != original or workstream is not None):
            raise BehaviorPolicyError("BEHAVIOR_EXPOSURE_TARGET_WIDENING")
        self._thread_scope.require_thread(project, target)
        if workstream is not None and not authenticated_data_scope_allows(
            {"workstream": workstream}
        ):
            raise ResourceScopeError("AUTH_DATA_SCOPE_DENIED")
        return target, workstream

    def prepare(
        self,
        *,
        project: str,
        proposal: ControlRecord,
        plan: ControlRecord,
        stage: str,
        scope: dict[str, str],
        budget: dict[str, JsonValue],
        rollback: dict[str, JsonValue],
    ) -> BehaviorExposureRecord | None:
        refs = plan.payload.get("result_refs")
        references = (
            cast(list[object] | tuple[object, ...], refs) if isinstance(refs, (list, tuple)) else ()
        )
        if self._paired is None or stage not in {"SHADOW", "CANARY"} or len(references) != 1:
            return None
        self._thread_scope.require_record(proposal)
        pair = self._paired.require_policy_evidence(project, str(references[0]))
        assert pair.result is not None
        if (
            pair.spec.plan_id != plan.record_id
            or pair.spec.candidate_ref != proposal.record_id
            or pair.spec.candidate_digest != proposal.payload.get("candidate_digest")
        ):
            raise BehaviorPolicyError("BEHAVIOR_EVALUATION_BINDING_MISMATCH")
        if stage == "CANARY" and (
            pair.result.performance_verdict != "BETTER"
            or pair.result.candidate_metrics.hard_failures
        ):
            raise BehaviorPolicyError("BEHAVIOR_IMPROVEMENT_REQUIRED")
        target, workstream = self._target(project, proposal, scope)
        content = proposal.payload.get("candidate_content")
        if not isinstance(content, dict):
            raise BehaviorPolicyError("BEHAVIOR_CANDIDATE_CONTENT_MISSING")
        content = cast(dict[str, object], content)
        component = pair.spec.component
        handler = self._components.resolve(component, str(content.get("version")))
        policy = handler.compile(content)
        if rollback.get("rollback_target_digest") != pair.spec.baseline_digest:
            raise BehaviorPolicyError("BEHAVIOR_ROLLBACK_TARGET_REQUIRED")
        governance = self._governance.read_policy(project)
        if governance is None:
            raise BehaviorPolicyError("BEHAVIOR_POLICY_NOT_FOUND")
        with self._ledger.transaction():
            self._paired.require_current_policy_evidence(pair)
            artifact = self._candidates.create_candidate(
                project_id=project,
                kind=component,
                content=content,
                parent_digest=pair.spec.baseline_digest,
                version=policy.version,
            )
            spec = BehaviorExposureSpec.model_validate(
                sealed_payload(
                    "BEHAVIOR_EXPOSURE_SPEC",
                    "spec_digest",
                    {
                        "exposure_id": self._ids.new("behavior-exposure"),
                        "project_id": project,
                        "component": component,
                        "environment": self._environment,
                        "evaluation_contract_ref": pair.spec.binding_id,
                        "scope_digest": domain_digest(
                            "BEHAVIOR_OVERLAP_SCOPE",
                            "1.0.0",
                            canonical_payload(
                                {
                                    "project_id": project,
                                    "component": component,
                                    "environment": self._environment,
                                    "evaluation_contract_ref": pair.spec.binding_id,
                                }
                            ),
                        ),
                        "stage": stage,
                        "improvement_ref": proposal.record_id,
                        "improvement_digest": proposal.record_digest,
                        "evaluation_plan_ref": plan.record_id,
                        "pair_id": pair.spec.pair_id,
                        "pair_result_digest": pair.result.result_digest,
                        "baseline_artifact_ref": pair.spec.baseline_ref,
                        "baseline_digest": pair.spec.baseline_digest,
                        "candidate_artifact_ref": artifact.artifact_id,
                        "candidate_digest": artifact.content_digest,
                        "candidate_revision_digest": pair.spec.candidate_digest,
                        "target_thread_id": target,
                        "target_workstream": workstream,
                        "policy_digest": governance.policy_digest,
                        "duration_seconds": self._budget(budget, "duration_seconds"),
                        "max_requests": self._budget(budget, "max_requests"),
                        "max_model_calls": self._budget(budget, "max_model_calls"),
                        "max_billed_cost_microunits": self._budget(
                            budget, "max_billed_cost_microunits"
                        ),
                        "comparison_scope": "DETERMINISTIC_COMPONENT_POLICY",
                        "model_quality": "NOT_ASSESSED",
                    },
                )
            )
            return self._write(self._prepared(spec, pair), 0)

    @staticmethod
    def _prepared(spec: BehaviorExposureSpec, pair: PairedRunRecord) -> BehaviorExposureRecord:
        assert pair.result is not None
        evidence = (
            BehaviorStageEvidence(
                stage="OFFLINE",
                status="EXECUTED",
                execution_refs=(
                    pair.result.baseline.execution_id,
                    pair.result.candidate.execution_id,
                ),
                receipt_refs=(pair.result.result_digest,),
            ),
            BehaviorStageEvidence(
                stage="SANDBOX",
                status="SKIPPED",
                reason_code="TRUSTED_DECLARATIVE_POLICY_NO_UNTRUSTED_CODE",
            ),
            *(
                (
                    BehaviorStageEvidence(
                        stage="SHADOW",
                        status="SKIPPED",
                        reason_code="POLICY_ONLY_COMPARISON_NO_MODEL_QUALITY_CLAIM",
                    ),
                )
                if spec.stage == "CANARY"
                else ()
            ),
            BehaviorStageEvidence(stage=spec.stage, status="PENDING"),
        )
        return BehaviorExposureRecord.model_validate(
            sealed_payload(
                "BEHAVIOR_EXPOSURE_RECORD",
                "record_digest",
                {
                    "spec": spec,
                    "revision": 1,
                    "state": "PREPARED",
                    "approval": None,
                    "started_at": None,
                    "expires_at": None,
                    "request_count": 0,
                    "model_call_count": 0,
                    "reserved_cost_microunits": 0,
                    "observations": (),
                    "stage_evidence": evidence,
                    "reason_code": None,
                },
            )
        )

    def _role_covers(self, role_scope: str, spec: BehaviorExposureSpec) -> bool:
        if role_scope == "PROJECT":
            return True
        workstream = spec.target_workstream
        if spec.target_thread_id is not None:
            thread = self._threads.read(spec.target_thread_id)
            workstream = None if thread is None else thread.scope.get("workstream")
        return workstream is not None and role_scope == "WORKSTREAM:" + workstream

    def _authority(self, spec: BehaviorExposureSpec, actor: str, role_ref: str) -> None:
        require_authenticated_authority(spec.project_id, actor, role_ref)
        role = next(
            (
                item
                for item in self._governance.list_roles(spec.project_id)
                if item.role_assignment_id == role_ref and item.actor_id == actor
            ),
            None,
        )
        if (
            role is None
            or role.state != "ACTIVE"
            or role.role != "improvement-owner"
            or not self._role_covers(role.scope, spec)
        ):
            raise BehaviorPolicyError("BEHAVIOR_APPROVAL_AUTHORITY_REQUIRED")

    def decide(
        self,
        project: str,
        identifier: str,
        *,
        expected_revision: int,
        approved_digest: str,
        actor_ref: str,
        role_ref: str,
        decision: Literal["APPROVE", "REJECT"],
    ) -> BehaviorExposureRecord:
        before = self.read(project, identifier)
        self._authority(before.spec, actor_ref, role_ref)
        evidence = self._evidence(before) if decision == "APPROVE" else None
        with self._ledger.transaction():
            record = self.read(project, identifier)
            self._authority(record.spec, actor_ref, role_ref)
            if record.revision != expected_revision or record.state != "PREPARED":
                raise BehaviorPolicyError("BEHAVIOR_EXPOSURE_REVISION_CONFLICT")
            if approved_digest != record.spec.spec_digest:
                raise BehaviorPolicyError("BEHAVIOR_EXACT_APPROVAL_REQUIRED")
            self.require_metadata(record)
            if evidence is not None and self._paired is not None:
                self._paired.require_current_policy_evidence(evidence)
            actor = current_authenticated_actor()
            approval = BehaviorApproval(
                actor_ref=actor_ref,
                role_assignment_ref=role_ref,
                session_ref=None if actor is None else actor.session_id,
                approved_digest=approved_digest,
                approved_at=self._clock.now(),
            )
            return self.transition(
                record,
                state="APPROVED" if decision == "APPROVE" else "REJECTED",
                approval=approval if decision == "APPROVE" else None,
            )

    def require_approval_current(self, record: BehaviorExposureRecord) -> None:
        approval = record.approval
        if record.spec.stage != "CANARY":
            return
        if approval is None:
            raise BehaviorPolicyError("BEHAVIOR_EXACT_APPROVAL_REQUIRED")
        role = next(
            (
                item
                for item in self._governance.list_roles(record.spec.project_id)
                if item.role_assignment_id == approval.role_assignment_ref
            ),
            None,
        )
        if (
            role is None
            or role.state != "ACTIVE"
            or role.actor_id != approval.actor_ref
            or role.role != "improvement-owner"
            or not self._role_covers(role.scope, record.spec)
        ):
            raise BehaviorPolicyError("BEHAVIOR_APPROVAL_REVOKED")
        if approval.session_ref is not None:
            session = self._sessions.read(approval.session_ref)
            if (
                session is None
                or session.state != "ACTIVE"
                or session.expires_at <= self._clock.now()
                or session.actor_id != approval.actor_ref
                or session.role_assignment_id != approval.role_assignment_ref
            ):
                raise BehaviorPolicyError("BEHAVIOR_APPROVAL_SESSION_EXPIRED")

    def require_metadata(self, record: BehaviorExposureRecord) -> None:
        spec = record.spec
        proposal = self._records.read(spec.project_id, "IMPROVEMENT", spec.improvement_ref)
        if proposal is None or proposal.record_digest != spec.improvement_digest:
            raise BehaviorPolicyError("BEHAVIOR_PROPOSAL_CHANGED")
        self._thread_scope.require_record(proposal)
        policy = self._governance.read_policy(spec.project_id)
        if policy is None or policy.policy_digest != spec.policy_digest:
            raise BehaviorPolicyError("BEHAVIOR_POLICY_CHANGED")
        for ref, digest in (
            (spec.baseline_artifact_ref, spec.baseline_digest),
            (spec.candidate_artifact_ref, spec.candidate_digest),
        ):
            artifact = self._behaviors.read(spec.project_id, ref)
            if (
                artifact is None
                or artifact.kind != spec.component
                or artifact.content_digest != digest
                or domain_digest(
                    "BEHAVIOR_ARTIFACT_CONTENT", "1.0.0", canonical_payload(artifact.content)
                )
                != digest
            ):
                raise BehaviorPolicyError("BEHAVIOR_ARTIFACT_CHANGED")
            self._components.resolve(artifact.kind, artifact.version).compile(artifact.content)

    def start(
        self, project: str, identifier: str, *, expected_revision: int
    ) -> BehaviorExposureRecord:
        before = self.read(project, identifier)
        if before.spec.stage == "CANARY" and before.state != "APPROVED":
            raise BehaviorPolicyError("BEHAVIOR_EXACT_APPROVAL_REQUIRED")
        evidence = self._evidence(before)
        with self._ledger.transaction():
            record = self.read(project, identifier)
            if record.revision != expected_revision:
                raise BehaviorPolicyError("BEHAVIOR_EXPOSURE_REVISION_CONFLICT")
            expected = "APPROVED" if record.spec.stage == "CANARY" else "PREPARED"
            if record.state != expected:
                raise BehaviorPolicyError(
                    "BEHAVIOR_EXACT_APPROVAL_REQUIRED"
                    if record.spec.stage == "CANARY"
                    else "BEHAVIOR_STAGE_NOT_READY"
                )
            self.require_metadata(record)
            self.require_approval_current(record)
            assert self._paired is not None
            self._paired.require_current_policy_evidence(evidence)
            now = self._clock.now()
            return self.transition(
                record,
                state="ARMED",
                started_at=now,
                expires_at=now + timedelta(seconds=record.spec.duration_seconds),
            )

    def _evidence(self, record: BehaviorExposureRecord) -> PairedRunRecord:
        if self._paired is None:
            raise BehaviorPolicyError("BEHAVIOR_POLICY_COMPARISON_REQUIRED")
        pair = self._paired.require_policy_evidence(record.spec.project_id, record.spec.pair_id)
        if pair.result is None or pair.result.result_digest != record.spec.pair_result_digest:
            raise BehaviorPolicyError("BEHAVIOR_EVALUATION_BINDING_MISMATCH")
        return pair

    def require_runtime_basis(self, record: BehaviorExposureRecord) -> None:
        self.require_metadata(record)
        self.require_approval_current(record)
        if self._paired is None:
            raise BehaviorPolicyError("BEHAVIOR_POLICY_COMPARISON_REQUIRED")
        pair = self._paired.policy_basis(record.spec.project_id, record.spec.pair_id)
        if pair.result is None or pair.result.result_digest != record.spec.pair_result_digest:
            raise BehaviorPolicyError("BEHAVIOR_EVALUATION_BINDING_MISMATCH")

    def verify_observations(self, record: BehaviorExposureRecord) -> None:
        if not record.observations or record.request_count != len(record.observations):
            raise BehaviorPolicyError("BEHAVIOR_OBSERVATION_REQUIRED")
        for observation in record.observations:
            stored = self._records.read(
                record.spec.project_id, "BEHAVIOR_RUNTIME", observation.execution_ref
            )
            if stored is None:
                raise BehaviorPolicyError("BEHAVIOR_OBSERVATION_REQUIRED")
            actual = BehaviorExecutionRecord.model_validate(stored.payload)
            snapshots = tuple(
                item
                for item in actual.snapshots
                if item.exposure_ref == record.spec.exposure_id and item.origin == record.spec.stage
            )
            digests = {item.snapshot_digest for item in snapshots}
            if (
                actual.receipt_digest != observation.execution_digest
                or not snapshots
                or not any(item.snapshot_digest in digests for item in actual.uses)
                or not observation.consumed
                or not observation.successful
            ):
                raise BehaviorPolicyError("BEHAVIOR_OBSERVATION_BINDING_MISMATCH")
            if record.spec.stage == "CANARY" and (
                actual.state != "COMPLETED"
                or not observation.decision_exposed
                or observation.billed_cost_microunits is None
            ):
                raise BehaviorPolicyError("BEHAVIOR_CANARY_OBSERVATION_REQUIRED")

    def complete(
        self, project: str, identifier: str, *, expected_revision: int
    ) -> BehaviorExposureRecord:
        before = self.read(project, identifier)
        evidence = self._evidence(before)
        with self._ledger.transaction():
            record = self.read(project, identifier)
            if record.revision != expected_revision or record.state != "ACTIVE":
                raise BehaviorPolicyError("BEHAVIOR_EXPOSURE_REVISION_CONFLICT")
            if record.expires_at is None or self._clock.now() > record.expires_at:
                return self.transition(
                    record, state="ROLLED_BACK", reason_code="BEHAVIOR_EXPOSURE_EXPIRED"
                )
            self.require_runtime_basis(record)
            self.verify_observations(record)
            assert self._paired is not None
            self._paired.require_current_policy_evidence(evidence)
            return self._complete_record(record, evidence)

    def complete_observed(self, record: BehaviorExposureRecord) -> BehaviorExposureRecord:
        if self._paired is None or record.state != "ACTIVE":
            raise BehaviorPolicyError("BEHAVIOR_OBSERVATION_REQUIRED")
        self.require_runtime_basis(record)
        self.verify_observations(record)
        evidence = self._paired.policy_basis(record.spec.project_id, record.spec.pair_id)
        return self._complete_record(record, evidence)

    def _complete_record(
        self, record: BehaviorExposureRecord, evidence: PairedRunRecord
    ) -> BehaviorExposureRecord:
        completed = self.transition(record, state="COMPLETED")
        if record.spec.stage == "CANARY":
            plan = self._records.read(
                record.spec.project_id, "IMPROVEMENT", record.spec.evaluation_plan_ref
            )
            if (
                plan is None
                or evidence.result is None
                or evidence.result.performance_verdict != "BETTER"
            ):
                raise BehaviorPolicyError("BEHAVIOR_IMPROVEMENT_REQUIRED")
            self._controls.create(
                project_id=record.spec.project_id,
                namespace="IMPROVEMENT",
                record_type="EVALUATION_PLAN",
                record_id=plan.record_id,
                state="EVALUATED",
                payload={
                    **plan.payload,
                    "evaluation_revision": plan.version + 1,
                    "promotion_eligible": True,
                    "runtime_exposure_ref": completed.spec.exposure_id,
                    "runtime_exposure_digest": completed.record_digest,
                    "runtime_observation_refs": tuple(
                        item.execution_ref for item in completed.observations
                    ),
                    "runtime_observation_digests": tuple(
                        item.execution_digest for item in completed.observations
                    ),
                    "exposure_state": "CANARY",
                },
            )
        return completed

    def rollback(
        self,
        project: str,
        identifier: str,
        *,
        expected_revision: int,
        actor_ref: str,
        role_ref: str,
    ) -> BehaviorExposureRecord:
        with self._ledger.transaction():
            record = self.read(project, identifier)
            self._authority(record.spec, actor_ref, role_ref)
            if record.revision != expected_revision:
                raise BehaviorPolicyError("BEHAVIOR_EXPOSURE_REVISION_CONFLICT")
            if record.state in {"ROLLED_BACK", "REJECTED", "SKIPPED"}:
                return record
            rolled = self.transition(
                record,
                state="REJECTED" if record.state == "PREPARED" else "ROLLED_BACK",
                reason_code="BEHAVIOR_OPERATOR_ROLLBACK",
            )
            self._restore_published_baseline(rolled, actor_ref)
            return rolled

    def _restore_published_baseline(self, record: BehaviorExposureRecord, actor: str) -> None:
        spec = record.spec
        scope_digest = domain_digest(
            "BEHAVIOR_BASELINE_SCOPE",
            "1.0.0",
            canonical_payload(
                {
                    "project_id": spec.project_id,
                    "component": spec.component,
                    "environment": spec.environment,
                    "target_thread_id": spec.target_thread_id,
                    "target_workstream": spec.target_workstream,
                }
            ),
        )
        current = self._store.baseline(
            spec.project_id, spec.component, spec.environment, scope_digest
        )
        if (
            current is None
            or current.exposure_ref != spec.exposure_id
            or current.content_digest != spec.candidate_digest
        ):
            return
        baseline = self._behaviors.read(spec.project_id, spec.baseline_artifact_ref)
        if (
            baseline is None
            or baseline.content_digest != spec.baseline_digest
            or domain_digest(
                "BEHAVIOR_ARTIFACT_CONTENT", "1.0.0", canonical_payload(baseline.content)
            )
            != spec.baseline_digest
        ):
            raise BehaviorPolicyError("BEHAVIOR_ROLLBACK_TARGET_CHANGED")
        self._components.resolve(baseline.kind, baseline.version).compile(baseline.content)
        restored = BehaviorBaselineRevision.model_validate(
            sealed_payload(
                "BEHAVIOR_BASELINE_REVISION",
                "receipt_digest",
                {
                    "project_id": spec.project_id,
                    "component": spec.component,
                    "environment": spec.environment,
                    "scope_digest": scope_digest,
                    "target_thread_id": spec.target_thread_id,
                    "target_workstream": spec.target_workstream,
                    "revision": current.revision + 1,
                    "change_kind": "ROLLED_BACK",
                    "artifact_ref": spec.baseline_artifact_ref,
                    "content_digest": spec.baseline_digest,
                    "previous_digest": current.content_digest,
                    "exposure_ref": spec.exposure_id,
                    "approval_ref": "exposure-spec:" + spec.spec_digest,
                    "actor_ref": actor,
                    "created_at": self._clock.now(),
                },
            )
        )
        self._store.rollback_baseline(restored, expected_revision=current.revision)

    def promotion_payload(
        self, assessments: tuple[ControlRecord | None, ...], proposal: ControlRecord
    ) -> dict[str, object]:
        refs = {
            item.payload.get("runtime_exposure_ref") for item in assessments if item is not None
        }
        refs.discard(None)
        if not refs:
            return {}
        if len(refs) != 1:
            raise BehaviorPolicyError("BEHAVIOR_PROMOTION_EVIDENCE_AMBIGUOUS")
        identifier = next(iter(refs))
        if not isinstance(identifier, str):
            raise BehaviorPolicyError("BEHAVIOR_PROMOTION_EVIDENCE_INVALID")
        record = self.read(proposal.project_id, identifier)
        if (
            record.state != "COMPLETED"
            or record.spec.stage != "CANARY"
            or record.spec.improvement_digest != proposal.record_digest
            or any(
                item is None or item.payload.get("runtime_exposure_digest") != record.record_digest
                for item in assessments
            )
        ):
            raise BehaviorPolicyError("BEHAVIOR_CANARY_EVIDENCE_REQUIRED")
        self.verify_observations(record)
        return {
            "runtime_exposure_ref": identifier,
            "runtime_exposure_digest": record.record_digest,
            "runtime_scope": record.spec.model_dump(mode="python"),
            "baseline_kind": "BEHAVIOR",
            "scientific_baseline_change": False,
        }

    def prepare_promotion(
        self, project: str, promotion_id: str, approved_digest: str
    ) -> PairedRunRecord | None:
        promotion = self._records.read(project, "IMPROVEMENT", promotion_id)
        if promotion is None:
            raise BehaviorPolicyError("BEHAVIOR_PROMOTION_NOT_FOUND")
        self._thread_scope.require_record(promotion)
        if promotion.record_digest != approved_digest or promotion.state != "PENDING":
            raise BehaviorPolicyError("BEHAVIOR_FINAL_APPROVAL_REQUIRED")
        identifier = promotion.payload.get("runtime_exposure_ref")
        if not isinstance(identifier, str):
            return None
        record = self.read(project, identifier)
        if record.state != "COMPLETED" or record.record_digest != promotion.payload.get(
            "runtime_exposure_digest"
        ):
            raise BehaviorPolicyError("BEHAVIOR_CANARY_EVIDENCE_REQUIRED")
        self.verify_observations(record)
        return self._evidence(record)

    def apply_promotion(
        self, promotion: ControlRecord, prepared: PairedRunRecord | None
    ) -> BehaviorBaselineRevision | None:
        identifier = promotion.payload.get("runtime_exposure_ref")
        if not isinstance(identifier, str):
            return None
        if prepared is None or self._paired is None or promotion.state != "APPROVED":
            raise BehaviorPolicyError("BEHAVIOR_FINAL_APPROVAL_REQUIRED")
        exposure = self.read(promotion.project_id, identifier)
        if (
            exposure.state != "COMPLETED"
            or exposure.record_digest != promotion.payload.get("runtime_exposure_digest")
            or exposure.spec.pair_id != prepared.spec.pair_id
        ):
            raise BehaviorPolicyError("BEHAVIOR_CANARY_EVIDENCE_REQUIRED")
        actor, role = (
            promotion.payload.get("actor_ref"),
            promotion.payload.get("role_assignment_ref"),
        )
        if not isinstance(actor, str) or not isinstance(role, str):
            raise BehaviorPolicyError("BEHAVIOR_FINAL_APPROVAL_REQUIRED")
        self._authority(exposure.spec, actor, role)
        self.require_metadata(exposure)
        self.verify_observations(exposure)
        self._paired.require_current_policy_evidence(prepared)
        spec = exposure.spec
        workstream = spec.target_workstream
        if spec.target_thread_id is not None:
            thread = self._threads.read(spec.target_thread_id)
            workstream = None if thread is None else thread.scope.get("workstream")
        current = select_behavior_baseline(
            self._store.baselines(spec.project_id, spec.component, spec.environment),
            spec.target_thread_id,
            workstream,
        )
        if current is not None and current.content_digest != spec.baseline_digest:
            raise BehaviorPolicyError("BEHAVIOR_BASELINE_CHANGED")
        scope_digest = domain_digest(
            "BEHAVIOR_BASELINE_SCOPE",
            "1.0.0",
            canonical_payload(
                {
                    "project_id": spec.project_id,
                    "component": spec.component,
                    "environment": spec.environment,
                    "target_thread_id": spec.target_thread_id,
                    "target_workstream": spec.target_workstream,
                }
            ),
        )
        exact = self._store.baseline(
            spec.project_id, spec.component, spec.environment, scope_digest
        )
        revision = 1 if exact is None else exact.revision + 1
        baseline = BehaviorBaselineRevision.model_validate(
            sealed_payload(
                "BEHAVIOR_BASELINE_REVISION",
                "receipt_digest",
                {
                    "project_id": spec.project_id,
                    "component": spec.component,
                    "environment": spec.environment,
                    "scope_digest": scope_digest,
                    "target_thread_id": spec.target_thread_id,
                    "target_workstream": spec.target_workstream,
                    "revision": revision,
                    "change_kind": "PROMOTED",
                    "artifact_ref": spec.candidate_artifact_ref,
                    "content_digest": spec.candidate_digest,
                    "previous_digest": spec.baseline_digest,
                    "exposure_ref": spec.exposure_id,
                    "approval_ref": promotion.control_revision_id,
                    "actor_ref": actor,
                    "created_at": self._clock.now(),
                },
            )
        )
        self._store.promote(baseline, expected_revision=revision - 1)
        return baseline
