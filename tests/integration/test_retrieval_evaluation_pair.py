"""S3: a retrieval comparison records its evaluation algorithm and never mixes algorithms."""

import json
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

import pytest
from tests.integration.paired_evaluation_helpers import PairHarness, pair_harness
from tests.unit.test_retrieval_selection_oracle import span

from thoth.adapters.behavior.registry import default_behavior_components
from thoth.adapters.storage import ContentAddressedObjectStore
from thoth.adapters.storage.behavior_artifact import SqliteBehaviorArtifactStore
from thoth.adapters.storage.evaluation_run import SqliteEvaluationRunStore
from thoth.application.services import paired_evaluation_service
from thoth.application.services.behavior_snapshot_service import BehaviorSnapshotService
from thoth.application.services.paired_evaluation_service import PairedEvaluationService
from thoth.apps.runtime import create_runtime
from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.behavior_policy import RetrievalBehaviorPolicy
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.retrieval_lane import RETRIEVAL_EVALUATOR_ID

SPANS = (
    span("a1", "alpha one short entry text padded here ok", line=1),
    span("a2", "alpha two a somewhat longer entry text padded here for the test", line=2),
    span("a3", "alpha three the longest of the entries with extra words so it ranks first", line=3),
)
BASELINE = {
    "kind": "RETRIEVAL_POLICY",
    "version": "2.0.0",
    "max_spans": 1,
    "character_budget": 50000,
}
CANDIDATE = {
    "kind": "RETRIEVAL_POLICY",
    "version": "3.0.0",
    "max_spans": 2,
    "character_budget": 50000,
    "lanes": [{"lane_id": "LEXICAL_SUBSTRING_V1", "lane_version": "1.0.0"}],
}
# Longest first: a3, then a2; the candidate may take two of the three.
EXPECTED: dict[str, object] = {
    "selected_refs": ["span:a3", "span:a2"],
    "excluded_count": 1,
    "selected_characters": len(SPANS[2].exact_text) + len(SPANS[1].exact_text),
    "mode": "LEGACY",
    "evaluator": RETRIEVAL_EVALUATOR_ID,
}
CASES: list[dict[str, object]] = [
    {
        "public": {
            "case_id": "retrieval",
            "payload": {"problem": "alpha", "evidence": [s.model_dump(mode="json") for s in SPANS]},
        },
        "expected_output": EXPECTED,
        "axis": "quality",
    }
]
IDENTITY = (
    "project_id",
    "plan_id",
    "plan_digest",
    "component",
    "baseline_ref",
    "baseline_digest",
    "candidate_ref",
    "candidate_digest",
    "binding_id",
    "binding_digest",
    "fixture_digest",
    "scorer_digest",
    "public_input_digest",
    "initial_memory_digest",
    "context",
    "resource_refs",
    "component_registry_digest",
    "max_requests",
    "max_output_bytes",
    "timeout_seconds",
)


def retrieval_pair(tmp_path: Path) -> AbstractAsyncContextManager[PairHarness]:
    return pair_harness(
        tmp_path,
        {},
        {},
        CASES,
        policy_pair=(BASELINE, CANDIDATE),
        component=BehaviorArtifactKind.RETRIEVAL_POLICY,
        target_component="RETRIEVAL_QUERY_ROUTING",
        executor_id="BEHAVIOR_COMPONENT_SUBPROCESS_V1",
    )


def dedupe_without_algorithm(spec: dict[str, Any]) -> str:
    identity = {key: spec[key] for key in IDENTITY}
    return domain_digest("EVALUATION_DEDUPE", "1.0.0", canonical_payload(identity))


async def test_a_retrieval_pair_runs_both_versions_and_records_the_algorithm(
    tmp_path: Path,
) -> None:
    async with retrieval_pair(tmp_path) as h:
        response = await h.run()
        assert response["state"] == "COMPLETE", response
        result = response["pair"]["result"]
        assert result["performance_verdict"] == "BETTER"
        store = ContentAddressedObjectStore(tmp_path)
        candidate = json.loads(store.read(result["candidate"]["output_blob_digest"]))
        baseline = json.loads(store.read(result["baseline"]["output_blob_digest"]))
        assert candidate["outputs"] == [EXPECTED]
        assert baseline["outputs"][0]["evaluator"] == RETRIEVAL_EVALUATOR_ID
        assert baseline["outputs"][0]["selected_refs"] == ["span:a3"]


async def test_the_algorithm_is_part_of_a_retrieval_pair_identity_and_no_spec_field(
    tmp_path: Path,
) -> None:
    async with retrieval_pair(tmp_path) as h:
        spec = (await h.run())["pair"]["spec"]
        with_algorithm = domain_digest(
            "EVALUATION_DEDUPE",
            "1.0.0",
            canonical_payload(
                {**{key: spec[key] for key in IDENTITY}, "evaluator": RETRIEVAL_EVALUATOR_ID}
            ),
        )
        assert spec["dedupe_digest"] == with_algorithm
        assert spec["dedupe_digest"] != dedupe_without_algorithm(spec)
        assert "evaluator" not in spec


async def test_another_component_keeps_the_identity_it_always_had(tmp_path: Path) -> None:
    policies = (
        {"kind": "PROMPT_BUNDLE", "version": "2.0.0", "task_guidance": "old"},
        {"kind": "PROMPT_BUNDLE", "version": "2.0.0", "task_guidance": "cite sources"},
    )
    cases: list[dict[str, object]] = [
        {
            "public": {"case_id": "policy", "payload": {}},
            "expected_output": {"behavior_guidance": "cite sources"},
            "axis": "quality",
        }
    ]
    async with pair_harness(
        tmp_path,
        {},
        {},
        cases,
        policy_pair=policies,
        component=BehaviorArtifactKind.PROMPT_BUNDLE,
        target_component="PROMPT",
        executor_id="BEHAVIOR_COMPONENT_SUBPROCESS_V1",
    ) as h:
        spec = (await h.run())["pair"]["spec"]
        # The rebuilt identity matches the stored digest, so the check above is a real one.
        assert spec["dedupe_digest"] == dedupe_without_algorithm(spec)


def interrupt_after_baseline(patch: pytest.MonkeyPatch) -> None:
    def interrupt(_service: PairedEvaluationService, label: str) -> None:
        if label == "after_baseline_receipt":
            raise RuntimeError("injected interruption")

    patch.setattr(PairedEvaluationService, "_checkpoint", interrupt)


async def test_a_resumed_pair_keeps_working_when_the_algorithm_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with retrieval_pair(tmp_path) as h:
        with monkeypatch.context() as patch:
            interrupt_after_baseline(patch)
            with pytest.raises(AssertionError):
                await h.run("first-interrupted")
        resumed = await h.run("resume")
        assert resumed["state"] == "COMPLETE", resumed


async def test_a_pair_whose_first_arm_came_from_another_algorithm_is_held_not_mixed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with retrieval_pair(tmp_path) as h:
        with monkeypatch.context() as patch:
            interrupt_after_baseline(patch)
            with pytest.raises(AssertionError):
                await h.run("first-interrupted")
        store = SqliteEvaluationRunStore(h.runtime.ledger.engine)
        partial = store.read_plan(h.project, h.plan["record_id"], h.plan["record_digest"])
        assert partial is not None and partial.state == "PARTIAL" and partial.baseline is not None

        # The baseline arm was made with 1.0.0; the code is now at another algorithm version.
        def another_algorithm(component: BehaviorArtifactKind) -> str:
            del component
            return "retrieval-lane-dispatch:2.0.0"

        monkeypatch.setattr(paired_evaluation_service, "evaluation_algorithm", another_algorithm)
        held = await h.run("resume-after-algorithm-change")
        assert held["state"] == "HELD", held
        assert held["pair"]["reason_code"] == "EVALUATION_ALGORITHM_CHANGED"
        stored = store.read_plan(h.project, h.plan["record_id"], h.plan["record_digest"])
        assert stored is not None and stored.state == "HELD" and stored.candidate is None


async def test_the_baseline_snapshot_is_still_retrieval_policy_2_0_0(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path)
    try:
        service = BehaviorSnapshotService(
            SqliteBehaviorArtifactStore(runtime.ledger.engine), default_behavior_components()
        )
        snapshots = service.capture("project:s3-baseline")
        retrieval = next(
            s for s in snapshots if s.component == BehaviorArtifactKind.RETRIEVAL_POLICY
        )
        assert type(retrieval.policy) is RetrievalBehaviorPolicy
        assert retrieval.policy.version == "2.0.0" and retrieval.origin == "BUILTIN_DEFAULT"
        assert retrieval.content_digest == (
            "af04aacf42bb7a2d0cdd637f9e6f8680d9bac394f41860080bad433dfa287719"
        )
    finally:
        runtime.close()
