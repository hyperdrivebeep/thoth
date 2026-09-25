"""Bounded use of approved canaries and non-authoritative policy shadows."""

from thoth.application.services.improvement_exposure_service import ImprovementExposureService
from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.behavior_execution import (
    ActiveBehaviorSnapshot,
    BehaviorBaselineRevision,
    BehaviorExecutionRecord,
    BehaviorExposureRecord,
    BehaviorObservation,
    BehaviorSelection,
    BehaviorStageEvidence,
    select_behavior_baseline,
)
from thoth.domain.behavior_policy import BehaviorPolicyError
from thoth.domain.evaluation_run import EvaluationRunError, sealed_payload
from thoth.domain.resource_scope import ResourceScopeError
from thoth.ports.behavior_artifact import BehaviorArtifactStorePort
from thoth.ports.behavior_execution import BehaviorExecutionStorePort
from thoth.ports.behavior_resolver import BehaviorComponentRegistryPort
from thoth.ports.ledger import LedgerPort
from thoth.ports.runtime import ClockPort
from thoth.ports.thread import ThreadStorePort


class BehaviorExposureRuntime:
    def __init__(
        self,
        *,
        service: ImprovementExposureService,
        store: BehaviorExecutionStorePort,
        behaviors: BehaviorArtifactStorePort,
        components: BehaviorComponentRegistryPort,
        threads: ThreadStorePort,
        ledger: LedgerPort,
        clock: ClockPort,
    ) -> None:
        self._service, self._store, self._behaviors, self._components = (
            service,
            store,
            behaviors,
            components,
        )
        self._threads, self._ledger, self._clock = threads, ledger, clock

    def _matches(
        self,
        target_thread: str | None,
        target_workstream: str | None,
        thread_id: str | None,
        workstream: str | None,
    ) -> bool:
        return (target_thread is None or target_thread == thread_id) and (
            target_workstream is None or target_workstream == workstream
        )

    def baseline(
        self, project: str, component: BehaviorArtifactKind, environment: str, thread_id: str | None
    ) -> BehaviorBaselineRevision | None:
        thread = None if thread_id is None else self._threads.read(thread_id)
        workstream = None if thread is None else thread.scope.get("workstream")
        return select_behavior_baseline(
            self._store.baselines(project, component, environment), thread_id, workstream
        )

    @staticmethod
    def _annotate(
        snapshot: ActiveBehaviorSnapshot, record: BehaviorExposureRecord, reason: str
    ) -> ActiveBehaviorSnapshot:
        data = snapshot.model_dump(mode="python", exclude={"snapshot_digest"})
        data.update(exposure_ref=record.spec.exposure_id, reason_code=reason)
        return ActiveBehaviorSnapshot.model_validate(
            sealed_payload("ACTIVE_BEHAVIOR_SNAPSHOT", "snapshot_digest", data)
        )

    def _snapshot(
        self, base: ActiveBehaviorSnapshot, record: BehaviorExposureRecord
    ) -> ActiveBehaviorSnapshot:
        artifact = self._behaviors.read(record.spec.project_id, record.spec.candidate_artifact_ref)
        if artifact is None:
            raise BehaviorPolicyError("BEHAVIOR_ARTIFACT_CHANGED")
        policy = self._components.resolve(artifact.kind, artifact.version).compile(artifact.content)
        data = base.model_dump(mode="python", exclude={"snapshot_digest"})
        data.update(
            artifact_ref=artifact.artifact_id,
            content_digest=artifact.content_digest,
            policy=policy,
            origin=record.spec.stage,
            registry_revision=record.revision,
            exposure_ref=record.spec.exposure_id,
            ignored_registry_digest=None,
            reason_code=None,
        )
        return ActiveBehaviorSnapshot.model_validate(
            sealed_payload("ACTIVE_BEHAVIOR_SNAPSHOT", "snapshot_digest", data)
        )

    def begin(
        self,
        project: str,
        thread: str,
        execution_id: str,
        baselines: tuple[ActiveBehaviorSnapshot, ...],
    ) -> BehaviorSelection:
        actual = self._threads.read(thread)
        workstream = None if actual is None else actual.scope.get("workstream")
        active: list[BehaviorExposureRecord] = []
        with self._ledger.transaction():
            for item in self._store.list(project):
                if item.state not in {"ARMED", "ACTIVE"}:
                    continue
                if item.expires_at is None or item.expires_at <= self._clock.now():
                    self._service.transition(
                        item, state="ROLLED_BACK", reason_code="BEHAVIOR_EXPOSURE_EXPIRED"
                    )
                    continue
                if self._matches(
                    item.spec.target_thread_id, item.spec.target_workstream, thread, workstream
                ):
                    active.append(item)
            snapshots: list[ActiveBehaviorSnapshot] = []
            shadows: list[ActiveBehaviorSnapshot] = []
            for base in baselines:
                matches = [
                    item
                    for item in active
                    if item.spec.component == base.component
                    and item.spec.environment == base.environment
                ]
                canaries = [item for item in matches if item.spec.stage == "CANARY"]
                if len(canaries) > 1:
                    raise BehaviorPolicyError("BEHAVIOR_CANARY_SELECTION_AMBIGUOUS")
                selected = base
                for item in matches:
                    if item.spec.baseline_digest != base.content_digest:
                        if item.spec.stage == "CANARY":
                            selected = self._annotate(
                                base, item, "BEHAVIOR_TARGET_BASELINE_DIFFERS"
                            )
                        continue
                    if item.request_count > len(item.observations):
                        if item.spec.stage == "CANARY":
                            selected = self._annotate(base, item, "BEHAVIOR_PRIOR_WORK_UNRESOLVED")
                        continue
                    if item.request_count >= item.spec.max_requests:
                        continue
                    try:
                        self._service.require_runtime_basis(item)
                    except (
                        BehaviorPolicyError,
                        EvaluationRunError,
                        ResourceScopeError,
                        ValueError,
                    ) as exc:
                        reason = getattr(exc, "code", "BEHAVIOR_CURRENT_BASIS_INVALID")
                        self._service.transition(item, state="ROLLED_BACK", reason_code=str(reason))
                        continue
                    item = self._service.transition(
                        item,
                        state="ACTIVE",
                        request_count=item.request_count + 1,
                        stage_evidence=(
                            *item.stage_evidence,
                            BehaviorStageEvidence(
                                stage=item.spec.stage,
                                status="RUNNING",
                                execution_refs=(execution_id,),
                            ),
                        ),
                    )
                    if item.spec.stage == "SHADOW":
                        shadows.append(self._snapshot(base, item))
                    else:
                        selected = self._snapshot(base, item)
                snapshots.append(selected)
        return BehaviorSelection(snapshots=tuple(snapshots), shadows=tuple(shadows))

    def before_model(
        self, snapshots: tuple[ActiveBehaviorSnapshot, ...], quote: int | None
    ) -> None:
        canaries = tuple(
            item for item in snapshots if item.origin == "CANARY" and item.exposure_ref is not None
        )
        try:
            with self._ledger.transaction():
                for snapshot in canaries:
                    assert snapshot.exposure_ref is not None
                    record = self._service.read(snapshot.project_id, snapshot.exposure_ref)
                    self._service.require_runtime_basis(record)
                    if (
                        record.state != "ACTIVE"
                        or record.expires_at is None
                        or record.expires_at <= self._clock.now()
                    ):
                        raise BehaviorPolicyError("BEHAVIOR_EXPOSURE_EXPIRED")
                    if quote is None or quote < 0:
                        raise BehaviorPolicyError("BEHAVIOR_MODEL_COST_UNKNOWN")
                    if record.model_call_count >= record.spec.max_model_calls:
                        raise BehaviorPolicyError("BEHAVIOR_MODEL_CALL_BUDGET_EXHAUSTED")
                    if (
                        record.reserved_cost_microunits + quote
                        > record.spec.max_billed_cost_microunits
                    ):
                        raise BehaviorPolicyError("BEHAVIOR_COST_BUDGET_EXHAUSTED")
                    self._service.transition(
                        record,
                        model_call_count=record.model_call_count + 1,
                        reserved_cost_microunits=record.reserved_cost_microunits + quote,
                    )
        except (BehaviorPolicyError, EvaluationRunError, ResourceScopeError, ValueError) as exc:
            with self._ledger.transaction():
                for snapshot in canaries:
                    assert snapshot.exposure_ref is not None
                    current = self._store.read(snapshot.project_id, snapshot.exposure_ref)
                    if current is not None and current.state in {"ARMED", "ACTIVE"}:
                        self._service.transition(
                            current,
                            state="ROLLED_BACK",
                            reason_code=str(getattr(exc, "code", "BEHAVIOR_CURRENT_BASIS_INVALID")),
                        )
            raise

    def finish(self, record: BehaviorExecutionRecord) -> None:
        for snapshot in record.snapshots:
            if snapshot.origin not in {"CANARY", "SHADOW"} or snapshot.exposure_ref is None:
                continue
            with self._ledger.transaction():
                exposure = self._store.read(record.project_id, snapshot.exposure_ref)
                if exposure is None or exposure.state not in {"ACTIVE", "ROLLED_BACK"}:
                    continue
                if any(item.execution_ref == record.execution_id for item in exposure.observations):
                    continue
                uses = tuple(
                    item for item in record.uses if item.snapshot_digest == snapshot.snapshot_digest
                )
                model_uses = tuple(item for item in record.uses if item.operation == "MODEL_INPUT")
                costs = tuple(item.billed_cost_microunits for item in model_uses)
                successful = (
                    bool(uses)
                    and all(item.operation != "POLICY_HELD" for item in uses)
                    and (record.state == "COMPLETED" or snapshot.origin == "SHADOW")
                )
                reason = (
                    None if successful else record.reason_code or "BEHAVIOR_POLICY_NOT_CONSUMED"
                )
                try:
                    self._service.require_runtime_basis(exposure)
                    if exposure.expires_at is None or self._clock.now() > exposure.expires_at:
                        raise BehaviorPolicyError("BEHAVIOR_EXPOSURE_EXPIRED")
                except (
                    BehaviorPolicyError,
                    EvaluationRunError,
                    ResourceScopeError,
                    ValueError,
                ) as exc:
                    successful, reason = (
                        False,
                        str(getattr(exc, "code", "BEHAVIOR_CURRENT_BASIS_INVALID")),
                    )
                observation = BehaviorObservation(
                    execution_ref=record.execution_id,
                    execution_digest=record.receipt_digest,
                    consumed=bool(uses),
                    successful=successful,
                    decision_exposed=successful
                    and snapshot.origin == "CANARY"
                    and bool(model_uses),
                    model_calls=0
                    if snapshot.origin == "SHADOW"
                    else sum(item.operation == "MODEL_DISPATCH" for item in record.uses),
                    billed_cost_microunits=0
                    if snapshot.origin == "SHADOW"
                    else None
                    if any(cost is None for cost in costs)
                    else sum(cost for cost in costs if cost is not None),
                    reason_code=reason,
                )
                evidence = BehaviorStageEvidence(
                    stage=exposure.spec.stage,
                    status="EXECUTED" if uses else "SKIPPED",
                    execution_refs=(record.execution_id,) if uses else (),
                    receipt_refs=(record.receipt_digest,) if uses else (),
                    reason_code=None if uses else "BEHAVIOR_POLICY_NOT_CONSUMED",
                )
                state = (
                    "ROLLED_BACK" if exposure.state == "ROLLED_BACK" or not successful else "ACTIVE"
                )
                reason = exposure.reason_code if exposure.state == "ROLLED_BACK" else reason
                updated = self._service.transition(
                    exposure,
                    state=state,
                    observations=(*exposure.observations, observation),
                    stage_evidence=(*exposure.stage_evidence, evidence),
                    reason_code=reason,
                )
                if (
                    updated.state == "ACTIVE"
                    and successful
                    and updated.request_count == updated.spec.max_requests
                    and len(updated.observations) == updated.request_count
                ):
                    self._service.complete_observed(updated)

    def validate_work(self, snapshots: tuple[ActiveBehaviorSnapshot, ...]) -> None:
        for snapshot in snapshots:
            if snapshot.origin != "CANARY" or snapshot.exposure_ref is None:
                continue
            try:
                record = self._service.read(snapshot.project_id, snapshot.exposure_ref)
                self._service.require_runtime_basis(record)
                if (
                    record.state != "ACTIVE"
                    or record.expires_at is None
                    or self._clock.now() > record.expires_at
                ):
                    raise BehaviorPolicyError("BEHAVIOR_EXPOSURE_EXPIRED")
            except (BehaviorPolicyError, EvaluationRunError, ResourceScopeError, ValueError) as exc:
                with self._ledger.transaction():
                    current = self._store.read(snapshot.project_id, snapshot.exposure_ref)
                    if current is not None and current.state in {"ARMED", "ACTIVE"}:
                        self._service.transition(
                            current,
                            state="ROLLED_BACK",
                            reason_code=str(getattr(exc, "code", "BEHAVIOR_CURRENT_BASIS_INVALID")),
                        )
                raise
