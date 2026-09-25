"""Resolve actual immutable content and bind current authority before paired I/O."""

from datetime import timedelta
from typing import cast

from pydantic import JsonValue

from thoth.application.services.behavior_snapshot_service import BehaviorSnapshotService
from thoth.application.services.improvement_evaluation_gate import ImprovementEvaluationGate
from thoth.application.services.improvement_evidence import plan_matches_improvement
from thoth.application.services.improvement_thread_scope import ImprovementThreadScope
from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.behavior_policy import BehaviorPolicyError
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.control_record import ControlRecord
from thoth.domain.evaluation_run import (
    BehaviorEvaluationProgram,
    CompiledEvaluationProgram,
    EvaluationBinding,
    EvaluationProgram,
    EvaluationRunError,
    EvaluationRunSpec,
    ExecutableArtifact,
    sealed_payload,
)
from thoth.ports.behavior_artifact import BehaviorArtifactStorePort
from thoth.ports.behavior_resolver import BehaviorComponentRegistryPort
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.evaluation_runner import EvaluationCatalogPort
from thoth.ports.resource_scope import ResourceAccessPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort

_TARGETS = {
    "PROMPT": BehaviorArtifactKind.PROMPT_BUNDLE,
    "RETRIEVAL_QUERY_ROUTING": BehaviorArtifactKind.RETRIEVAL_POLICY,
    "CONTEXT_SELECTION_POLICY": BehaviorArtifactKind.RETRIEVAL_POLICY,
    "WORKFLOW_GATE_ORDER": BehaviorArtifactKind.WORKFLOW_DEFINITION,
    "EVALUATOR_CODE": BehaviorArtifactKind.EVALUATOR_CONTRACT,
}


class EvaluationInputResolver:
    def __init__(
        self,
        *,
        behaviors: BehaviorArtifactStorePort,
        records: ControlRecordStorePort,
        catalog: EvaluationCatalogPort,
        gate: ImprovementEvaluationGate,
        access: ResourceAccessPort,
        clock: ClockPort,
        ids: IdGeneratorPort,
        thread_scope: ImprovementThreadScope,
        behavior_components: BehaviorComponentRegistryPort,
        behavior_snapshots: BehaviorSnapshotService,
    ) -> None:
        self._behaviors, self._records, self._catalog = behaviors, records, catalog
        self._gate, self._access, self._clock, self._ids = gate, access, clock, ids
        self._thread_scope = thread_scope
        self._behavior_components = behavior_components
        self._behavior_snapshots = behavior_snapshots

    def _registry_digest(self, project_id: str, component: BehaviorArtifactKind) -> str | None:
        registry = self._behaviors.read_registry(project_id, component)
        return (
            None
            if registry is None
            else domain_digest(
                "EVALUATION_COMPONENT_REGISTRY", "1.0.0", canonical_payload(registry)
            )
        )

    def _artifacts(
        self, project_id: str, plan: ControlRecord, component: BehaviorArtifactKind
    ) -> tuple[ExecutableArtifact, ExecutableArtifact]:
        improvement = self._records.read(
            project_id, "IMPROVEMENT", str(plan.payload.get("improvement_revision_id"))
        )
        if improvement is None or not plan_matches_improvement(plan, improvement):
            raise EvaluationRunError("EVALUATION_PLAN_BINDING_MISMATCH")
        self._thread_scope.require_record(improvement)
        if _TARGETS.get(str(improvement.payload.get("target_component"))) != component:
            raise EvaluationRunError("EVALUATION_COMPONENT_MISMATCH")
        matches = [
            value
            for value in self._behaviors.list(project_id, component)
            if value.content_digest == plan.payload.get("baseline_digest")
            and value.state in {"BASELINE", "ACTIVE"}
        ]
        thread_ref = improvement.payload.get("thread_id")
        active = (
            None
            if matches or component not in self._behavior_components.components()
            else self._behavior_snapshots.baseline_snapshot(
                project_id, component, thread_ref if isinstance(thread_ref, str) else None
            )
        )
        if (
            active is not None
            and active.artifact_ref is not None
            and active.content_digest == plan.payload.get("baseline_digest")
        ):
            current = self._behaviors.read(project_id, active.artifact_ref)
            if current is not None and all(
                item.artifact_id != current.artifact_id for item in matches
            ):
                matches.append(current)
        if len(matches) != 1:
            raise EvaluationRunError("EVALUATION_BASELINE_UNRESOLVED")
        baseline = matches[0]
        content = improvement.payload.get("candidate_content")
        if not isinstance(content, dict):
            raise EvaluationRunError("EVALUATION_CANDIDATE_CONTENT_MISSING")
        return (
            ExecutableArtifact(
                project_id=project_id,
                artifact_ref=baseline.artifact_id,
                component=component,
                content=cast(dict[str, JsonValue], baseline.content),
                content_digest=baseline.content_digest,
                digest_domain="BEHAVIOR_ARTIFACT_CONTENT",
            ),
            ExecutableArtifact(
                project_id=project_id,
                artifact_ref=improvement.record_id,
                component=component,
                content=cast(dict[str, JsonValue], content),
                content_digest=str(improvement.payload["candidate_digest"]),
                digest_domain="IMPROVEMENT_CANDIDATE",
            ),
        )

    def program(self, artifact: ExecutableArtifact) -> CompiledEvaluationProgram:
        if artifact.component in {
            BehaviorArtifactKind.CODE_PATCH,
            BehaviorArtifactKind.TRAINING_DATA,
        }:
            raise EvaluationRunError("EVALUATION_COMPONENT_UNSUPPORTED")
        if artifact.content.get("kind") != artifact.component.value:
            raise EvaluationRunError("EVALUATION_ARTIFACT_NOT_EXECUTABLE")
        if "evaluation_program" not in artifact.content:
            try:
                handler = self._behavior_components.resolve(
                    artifact.component, str(artifact.content.get("version"))
                )
                return BehaviorEvaluationProgram(
                    policy=handler.compile(cast(dict[str, object], artifact.content))
                )
            except BehaviorPolicyError as exc:
                raise EvaluationRunError(exc.code) from None
        if not isinstance(artifact.content["evaluation_program"], dict):
            raise EvaluationRunError("EVALUATION_ARTIFACT_NOT_EXECUTABLE")
        try:
            return EvaluationProgram.model_validate(artifact.content["evaluation_program"])
        except ValueError as exc:
            raise EvaluationRunError("EVALUATION_PROGRAM_INVALID") from exc

    def prepare(
        self, project_id: str, plan: ControlRecord, binding_id: str
    ) -> tuple[
        EvaluationRunSpec, EvaluationBinding, CompiledEvaluationProgram, CompiledEvaluationProgram
    ]:
        if plan.project_id != project_id or plan.state != "PLANNED":
            raise EvaluationRunError("EVALUATION_PLAN_NOT_READY")
        binding = self._catalog.resolve(project_id, binding_id)
        if binding.case_pack.expected_values_exposed:
            raise EvaluationRunError("EVALUATION_HOLDOUT_EXPOSED")
        datasets = plan.payload.get("datasets", ())
        if not isinstance(datasets, tuple | list) or not any(
            isinstance(item, dict)
            and cast(dict[str, object], item).get("fixture_digest") == binding.case_pack.pack_digest
            for item in cast(tuple[object, ...] | list[object], datasets)
        ):
            raise EvaluationRunError("EVALUATION_DATASET_BINDING_MISMATCH")
        if binding.scorer.scorer_id not in cast(list[str], plan.payload.get("evaluator_refs", ())):
            raise EvaluationRunError("EVALUATION_SCORER_BINDING_MISMATCH")
        if len(binding.case_pack.cases) * 2 > binding.max_requests:
            raise EvaluationRunError("EVALUATION_REQUEST_BUDGET_EXHAUSTED")
        self._access.require_reads(project_id, binding.case_pack.resource_refs)
        baseline, candidate = self._artifacts(project_id, plan, binding.component)
        if candidate.content_digest == binding.scorer.scorer_digest:
            raise EvaluationRunError("EVALUATION_SELF_SCORING_PROHIBITED")
        bp, cp = self.program(baseline), self.program(candidate)
        context = self._gate.capture(project_id)
        inputs = {
            "cases": [case.public.model_dump(mode="python") for case in binding.case_pack.cases]
        }
        identity: dict[str, object] = {
            "project_id": project_id,
            "plan_id": plan.record_id,
            "plan_digest": plan.record_digest,
            "component": binding.component,
            "baseline_ref": baseline.artifact_ref,
            "baseline_digest": baseline.content_digest,
            "candidate_ref": candidate.artifact_ref,
            "candidate_digest": candidate.content_digest,
            "binding_id": binding.binding_id,
            "binding_digest": binding.binding_digest,
            "fixture_digest": binding.case_pack.pack_digest,
            "scorer_digest": binding.scorer.scorer_digest,
            "public_input_digest": domain_digest(
                "EVALUATION_PUBLIC_INPUTS", "1.0.0", canonical_payload(inputs)
            ),
            "initial_memory_digest": domain_digest(
                "EVALUATION_MEMORY", "1.0.0", canonical_payload(binding.case_pack.initial_memory)
            ),
            "context": context,
            "resource_refs": binding.case_pack.resource_refs,
            "component_registry_digest": self._registry_digest(project_id, binding.component),
            "max_requests": binding.max_requests,
            "max_output_bytes": binding.max_output_bytes,
            "timeout_seconds": binding.timeout_seconds,
        }
        now = self._clock.now()
        spec = EvaluationRunSpec.model_validate(
            sealed_payload(
                "EVALUATION_RUN_SPEC",
                "request_digest",
                {
                    **identity,
                    "pair_id": self._ids.new("evaluation-pair"),
                    "created_at": now,
                    "deadline_at": now + timedelta(seconds=binding.timeout_seconds),
                    "dedupe_digest": domain_digest(
                        "EVALUATION_DEDUPE", "1.0.0", canonical_payload(identity)
                    ),
                },
            )
        )
        return spec, binding, bp, cp

    def validate_current(
        self, spec: EvaluationRunSpec, *, check_deadline: bool = True
    ) -> EvaluationBinding:
        binding = self._catalog.resolve(spec.project_id, spec.binding_id)
        if (
            binding.binding_digest != spec.binding_digest
            or binding.case_pack.expected_values_exposed
        ):
            raise EvaluationRunError("EVALUATION_BINDING_CHANGED")
        plan = self._records.read(spec.project_id, "IMPROVEMENT", spec.plan_id)
        if plan is None or plan.record_digest != spec.plan_digest:
            raise EvaluationRunError("EVALUATION_PLAN_CHANGED")
        baseline, candidate = self._artifacts(spec.project_id, plan, spec.component)
        if (
            baseline.artifact_ref,
            baseline.content_digest,
            candidate.artifact_ref,
            candidate.content_digest,
        ) != (spec.baseline_ref, spec.baseline_digest, spec.candidate_ref, spec.candidate_digest):
            raise EvaluationRunError("EVALUATION_ARTIFACT_CHANGED")
        try:
            current_context = self._gate.capture(spec.project_id)
        except ValueError:
            raise EvaluationRunError("EVALUATION_CONTEXT_CHANGED") from None
        if (
            current_context != spec.context
            or self._registry_digest(spec.project_id, spec.component)
            != spec.component_registry_digest
        ):
            raise EvaluationRunError("EVALUATION_CONTEXT_CHANGED")
        self._access.require_reads(spec.project_id, spec.resource_refs)
        if check_deadline and self._clock.now() >= spec.deadline_at:
            raise EvaluationRunError("EVALUATION_DEADLINE_EXPIRED")
        return binding

    def require_read(self, spec: EvaluationRunSpec) -> None:
        plan = self._records.read_digest(spec.project_id, spec.plan_digest)
        if plan is None or plan.record_id != spec.plan_id:
            raise EvaluationRunError("EVALUATION_PLAN_BINDING_MISMATCH")
        self._thread_scope.require_record(plan)
        self._access.require_reads(spec.project_id, spec.resource_refs)

    def validate_policy_evidence(self, spec: EvaluationRunSpec) -> EvaluationBinding:
        """Completed policy evidence tolerates its own annotation and later working revisions."""
        self.require_read(spec)
        binding = self._catalog.resolve(spec.project_id, spec.binding_id)
        if (
            binding.binding_digest != spec.binding_digest
            or binding.case_pack.expected_values_exposed
        ):
            raise EvaluationRunError("EVALUATION_BINDING_CHANGED")
        original = self._records.read_digest(spec.project_id, spec.plan_digest)
        current = self._records.read(spec.project_id, "IMPROVEMENT", spec.plan_id)
        if original is None or current is None:
            raise EvaluationRunError("EVALUATION_PLAN_BINDING_MISMATCH")
        keys = (
            "improvement_revision_id",
            "improvement_record_digest",
            "baseline_digest",
            "candidate_digest",
            "datasets",
            "evaluator_refs",
            "guardrails",
            "exposure_policy_ref",
        )
        if any(current.payload.get(key) != original.payload.get(key) for key in keys):
            raise EvaluationRunError("EVALUATION_PLAN_CHANGED")
        baseline, candidate = self._artifacts(spec.project_id, original, spec.component)
        if (
            baseline.content_digest != spec.baseline_digest
            or candidate.content_digest != spec.candidate_digest
            or not isinstance(self.program(baseline), BehaviorEvaluationProgram)
            or not isinstance(self.program(candidate), BehaviorEvaluationProgram)
        ):
            raise EvaluationRunError("BEHAVIOR_POLICY_COMPARISON_REQUIRED")
        now = self._gate.capture(spec.project_id)
        if (now.project_digest, now.policy_id, now.policy_revision, now.policy_digest) != (
            spec.context.project_digest,
            spec.context.policy_id,
            spec.context.policy_revision,
            spec.context.policy_digest,
        ):
            raise EvaluationRunError("EVALUATION_POLICY_CONTEXT_CHANGED")
        return binding
