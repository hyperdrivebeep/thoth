"""One bounded policy variant per frozen trigger; expected values stay scorer-only."""

from thoth.application.services.behavior_snapshot_service import BehaviorSnapshotService
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.evaluation_producer import SelectedEvaluation
from thoth.application.services.improvement_thread_scope import ImprovementThreadScope
from thoth.domain.behavior_artifact import (
    BehaviorArtifact,
    BehaviorArtifactKind,
    BehaviorArtifactState,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.control_record import ControlRecord
from thoth.domain.evaluation_run import EvaluationRunError, PairedRunRecord
from thoth.ports.behavior_artifact import BehaviorArtifactStorePort
from thoth.ports.behavior_resolver import BehaviorComponentRegistryPort
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.evaluation_runner import EvaluationCatalogPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort

_TARGETS = {
    BehaviorArtifactKind.PROMPT_BUNDLE: "PROMPT",
    BehaviorArtifactKind.RETRIEVAL_POLICY: "RETRIEVAL_QUERY_ROUTING",
    BehaviorArtifactKind.WORKFLOW_DEFINITION: "WORKFLOW_GATE_ORDER",
}


class BoundedBehaviorCandidateGenerator:
    def __init__(
        self,
        *,
        records: ControlRecordStorePort,
        controls: ControlRecordService,
        behaviors: BehaviorArtifactStorePort,
        snapshots: BehaviorSnapshotService,
        components: BehaviorComponentRegistryPort,
        catalog: EvaluationCatalogPort,
        thread_scope: ImprovementThreadScope,
        clock: ClockPort,
        ids: IdGeneratorPort,
    ) -> None:
        self._records, self._controls, self._behaviors = records, controls, behaviors
        self._snapshots, self._components, self._catalog = snapshots, components, catalog
        self._scope, self._clock, self._ids = thread_scope, clock, ids

    def generate(
        self, project: str, thread: str, fingerprint: str, failure_class: str
    ) -> SelectedEvaluation | None:
        self._scope.require_thread(project, thread)
        for snapshot in self._snapshots.capture(project, thread):
            binding = self._catalog.default_binding(project, snapshot.component)
            if binding is None:
                continue
            if binding.project_id != project or binding.component != snapshot.component:
                raise EvaluationRunError("EVALUATION_BINDING_PROJECT_MISMATCH")
            handler = self._components.resolve(snapshot.component, snapshot.policy.version)
            variants = handler.propose(snapshot.policy, failure_class)
            if not variants:
                continue
            trigger = domain_digest(
                "BEHAVIOR_CANDIDATE_TRIGGER",
                "1.0.0",
                canonical_payload(
                    {
                        "project_id": project,
                        "thread_id": thread,
                        "failure_fingerprint": fingerprint,
                        "baseline_digest": snapshot.content_digest,
                        "binding_digest": binding.binding_digest,
                    }
                ),
            )
            if any(
                item.payload.get("automatic_trigger_key") == trigger
                for item in self._records.list(project, "IMPROVEMENT", "REVISION")
            ):
                continue
            baseline_ref = snapshot.artifact_ref
            if baseline_ref is None:
                baseline_ref = self._ids.new("behavior-baseline")
                self._behaviors.add(
                    BehaviorArtifact(
                        artifact_id=baseline_ref,
                        project_id=project,
                        kind=snapshot.component,
                        version=snapshot.policy.version,
                        state=BehaviorArtifactState.BASELINE,
                        content=snapshot.policy.model_dump(mode="python"),
                        content_digest=snapshot.content_digest,
                        created_at=self._clock.now(),
                    )
                )
            policy = handler.compile(variants[0].model_dump(mode="python"))
            content = policy.model_dump(mode="python")
            candidate_digest = domain_digest(
                "IMPROVEMENT_CANDIDATE", "1.0.0", canonical_payload(content)
            )
            proposal = self._controls.create(
                project_id=project,
                namespace="IMPROVEMENT",
                record_type="REVISION",
                state="PROPOSED",
                payload={
                    "thread_id": thread,
                    "target_component": _TARGETS[snapshot.component],
                    "scope_key": "automatic-policy:" + trigger,
                    "automatic_trigger_key": trigger,
                    "trigger_refs": (fingerprint,),
                    "baseline_digest": snapshot.content_digest,
                    "candidate_content": content,
                    "candidate_digest": candidate_digest,
                    "improvement_hypothesis": (
                        "Compare one bounded registered policy variant; benefit is not presumed."
                    ),
                    "evaluation_contract_ref": binding.binding_id,
                    "candidate_lifecycle": "PROPOSED",
                    "evaluation_validity": "NOT_ASSESSED",
                    "performance_verdict": "INCONCLUSIVE",
                    "exposure_state": "NONE",
                    "promotion_state": "NOT_ELIGIBLE",
                    "overlap_lock": (),
                },
            )
            plan = self._controls.create(
                project_id=project,
                namespace="IMPROVEMENT",
                record_type="EVALUATION_PLAN",
                state="PLANNED",
                payload={
                    "improvement_revision_id": proposal.record_id,
                    "improvement_record_digest": proposal.record_digest,
                    "baseline_digest": snapshot.content_digest,
                    "candidate_digest": candidate_digest,
                    "datasets": ({"fixture_digest": binding.case_pack.pack_digest},),
                    "evaluator_refs": (binding.scorer.scorer_id,),
                    "guardrails": (),
                    "exposure_policy_ref": "bounded-policy-offline",
                    "evaluator_isolation": True,
                    "hidden_expected_value_access": False,
                    "evaluation_revision": 1,
                    "results": (),
                    "automatic_trigger_key": trigger,
                },
            )
            return SelectedEvaluation(plan, binding.binding_id)
        return None

    def record_evaluation(
        self, plan: ControlRecord, run: PairedRunRecord | None, reason: str | None
    ) -> None:
        if not isinstance(plan.payload.get("automatic_trigger_key"), str):
            return
        result = None if run is None else run.result
        self._controls.create(
            project_id=plan.project_id,
            namespace="IMPROVEMENT",
            record_type="EVALUATION_PLAN",
            record_id=plan.record_id,
            state="HELD" if result is None else "EVALUATED",
            payload={
                **plan.payload,
                "evaluation_revision": plan.version + 1,
                "result_refs": () if run is None else (run.spec.pair_id,),
                "evaluator_result_refs": () if result is None else (result.result_digest,),
                "exposure_ledger_ref": None if result is None else result.exposure_id,
                "evaluation_validity": "INCONCLUSIVE" if result is None else result.validity,
                "performance_verdict": "INCONCLUSIVE"
                if result is None
                else result.performance_verdict,
                "promotion_eligible": False,
                "reason_code": reason,
            },
        )
