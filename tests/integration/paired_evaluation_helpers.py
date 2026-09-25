from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.evaluators.catalog import FrozenEvaluationCatalog
from thoth.adapters.runtime import SystemClock
from thoth.adapters.storage.behavior_artifact import SqliteBehaviorArtifactStore
from thoth.apps.runtime import AppRuntime, create_runtime
from thoth.apps.runtime_types import RuntimeOptions
from thoth.domain.behavior_artifact import (
    BehaviorArtifact,
    BehaviorArtifactKind,
    BehaviorArtifactState,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evaluation_run import (
    EvaluationBinding,
    EvaluationCasePack,
    EvaluationScorer,
    sealed_payload,
)


@dataclass
class PairHarness:
    runtime: AppRuntime
    project: str
    binding: EvaluationBinding
    plan: dict[str, Any]
    proposal: dict[str, Any]

    async def run(self, key: str = "execute-pair") -> dict[str, Any]:
        return value(
            await self.runtime.bus.dispatch(
                request(
                    "improvement/evaluation/run",
                    key,
                    {
                        "project_id": self.project,
                        "evaluation_plan_id": self.plan["record_id"],
                        "expected_evaluation_revision": self.plan["version"],
                        "binding_id": self.binding.binding_id,
                    },
                )
            )
        )


@asynccontextmanager
async def pair_harness(
    workspace: Path,
    baseline_expression: Mapping[str, object],
    candidate_expression: Mapping[str, object],
    cases: Sequence[Mapping[str, object]],
    *,
    initial_memory: dict[str, object] | None = None,
    candidate_memory_updates: dict[str, object] | None = None,
    baseline_memory_updates: dict[str, object] | None = None,
    runtime_options: RuntimeOptions | None = None,
    thread_id: str | None = None,
    timeout_seconds: int = 30,
    expected_values_exposed: bool = False,
    max_pair_runs: int = 2,
    max_requests: int = 128,
    policy_pair: tuple[Mapping[str, object], Mapping[str, object]] | None = None,
    component: BehaviorArtifactKind = BehaviorArtifactKind.WORKFLOW_DEFINITION,
    target_component: str = "WORKFLOW_GATE_ORDER",
    executor_id: str = "PURE_TRANSFORM_SUBPROCESS_V1",
) -> AsyncGenerator[PairHarness]:
    project = "project:paired-execution"
    pack = EvaluationCasePack.model_validate(
        sealed_payload(
            "EVALUATION_CASE_PACK",
            "pack_digest",
            {
                "project_id": project,
                "pack_id": "private-case-pack",
                "version": "1.0.0",
                "cases": cases,
                "initial_memory": initial_memory or {},
                "resource_refs": (),
                "expected_visibility": "SCORER_ONLY",
                "expected_values_exposed": expected_values_exposed,
                "max_pair_runs": max_pair_runs,
            },
        )
    )
    scorer = EvaluationScorer.model_validate(
        sealed_payload(
            "EVALUATION_SCORER",
            "scorer_digest",
            {
                "scorer_id": "exact-json:1",
                "version": "1.0.0",
                "algorithm": "EXACT_JSON_V1",
            },
        )
    )
    binding = EvaluationBinding.model_validate(
        sealed_payload(
            "EVALUATION_BINDING",
            "binding_digest",
            {
                "project_id": project,
                "binding_id": "paired-contract:1",
                "component": component,
                "executor_id": executor_id,
                "case_pack": pack,
                "scorer": scorer,
                "max_requests": max_requests,
                "timeout_seconds": timeout_seconds,
                "max_output_bytes": 262144,
            },
        )
    )
    options: RuntimeOptions = {**(runtime_options or {})}
    options["evaluation_catalog"] = FrozenEvaluationCatalog((binding,))
    runtime = create_runtime(workspace, **options)
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "pair-project",
                    {
                        "project_id": project,
                        "name": "Paired evaluation",
                        "cutoff_at": "2026-09-01T00:00:00Z",
                    },
                )
            )
        )
        baseline_content: dict[str, object] = {
            "kind": "WORKFLOW_DEFINITION",
            "version": "1.0.0",
            "evaluation_program": {
                "outputs": {"answer": baseline_expression},
                "memory_updates": baseline_memory_updates or {},
            },
        }
        if policy_pair is not None:
            baseline_content = dict(policy_pair[0])
        baseline_digest = domain_digest(
            "BEHAVIOR_ARTIFACT_CONTENT", "1.0.0", canonical_payload(baseline_content)
        )
        SqliteBehaviorArtifactStore(runtime.ledger.engine).add(
            BehaviorArtifact(
                artifact_id="behavior:paired-baseline",
                project_id=project,
                kind=component,
                version=str(baseline_content["version"]),
                state=BehaviorArtifactState.BASELINE,
                content=baseline_content,
                content_digest=baseline_digest,
                created_at=SystemClock().now(),
            )
        )
        candidate_content = {
            "kind": "WORKFLOW_DEFINITION",
            "version": "1.0.1",
            "evaluation_program": {
                "outputs": {"answer": candidate_expression},
                "memory_updates": candidate_memory_updates or {},
            },
        }
        if policy_pair is not None:
            candidate_content = dict(policy_pair[1])
        await create_pair_thread(runtime, project, thread_id)
        proposal = value(
            await runtime.bus.dispatch(
                request(
                    "improvement/propose",
                    "pair-proposal",
                    {
                        "project_id": project,
                        "target_component": target_component,
                        "scope_key": "offline:pair",
                        "trigger_refs": [],
                        "baseline_digest": baseline_digest,
                        "candidate_content": candidate_content,
                        "improvement_hypothesis": "Compare the actual returned values",
                        "evaluation_contract_ref": binding.binding_id,
                        "thread_id": thread_id,
                    },
                )
            )
        )
        improvement = proposal["improvement"]
        plan = value(
            await runtime.bus.dispatch(
                request(
                    "improvement/evaluation/plan",
                    "pair-plan",
                    {
                        "project_id": project,
                        "improvement_revision_id": improvement["record_id"],
                        "baseline_digest": baseline_digest,
                        "datasets": [{"fixture_digest": pack.pack_digest}],
                        "evaluator_refs": [scorer.scorer_id],
                        "guardrails": [],
                        "exposure_policy_ref": "offline-only",
                    },
                )
            )
        )["evaluation_plan"]
        harness = PairHarness(runtime, project, binding, plan, improvement)
        try:
            yield harness
        finally:
            if harness.runtime is not runtime:
                harness.runtime.close()
    finally:
        runtime.close()


async def create_pair_thread(runtime: AppRuntime, project: str, thread_id: str | None) -> None:
    if thread_id is not None:
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "pair-thread",
                    {
                        "project_id": project,
                        "thread_id": thread_id,
                        "problem": "Run a safe R2 check.",
                        "scope": {"workstream": "normal-pair"},
                    },
                )
            )
        )
