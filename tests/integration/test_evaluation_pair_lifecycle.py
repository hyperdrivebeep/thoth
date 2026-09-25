"""Public pair lifecycle: changed bindings, durable resume, concurrency and I/O boundaries."""

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.integration.paired_evaluation_helpers import pair_harness
from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.evaluators.catalog import FrozenEvaluationCatalog
from thoth.adapters.evaluators.isolated_runner import IsolatedProgramExecutor
from thoth.adapters.runtime import SystemClock
from thoth.adapters.storage import ContentAddressedObjectStore, transaction
from thoth.adapters.storage.evaluation_run import SqliteEvaluationRunStore
from thoth.application.services.paired_evaluation_service import PairedEvaluationService
from thoth.application.services.resource_scope_service import ResourceScopeService
from thoth.apps.runtime import create_runtime
from thoth.domain.evaluation_run import EvaluationBinding, EvaluationScorer, sealed_payload
from thoth.domain.resource_scope import ResourceScopeError

CASES: list[dict[str, object]] = [
    {
        "public": {"case_id": "one", "payload": {}},
        "expected_output": {"answer": 1},
        "axis": "quality",
    }
]
BASE = {"op": "literal", "value": 0}
CANDIDATE = {"op": "literal", "value": 1}


@pytest.mark.parametrize("change", ["scorer", "deadline"])
async def test_change_after_baseline_prevents_candidate_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    async with pair_harness(tmp_path, BASE, CANDIDATE, CASES) as h:
        original_now = SystemClock.now
        original_resolve = FrozenEvaluationCatalog.resolve
        changed = False

        def checkpoint(_service: PairedEvaluationService, label: str) -> None:
            nonlocal changed
            if label == "after_baseline_receipt":
                changed = True

        def resolve(catalog: FrozenEvaluationCatalog, project: str, binding: str):
            result = original_resolve(catalog, project, binding)
            if changed and change == "scorer":
                scorer = EvaluationScorer.model_validate(
                    sealed_payload(
                        "EVALUATION_SCORER",
                        "scorer_digest",
                        {"scorer_id": "replacement", "version": "2", "algorithm": "EXACT_JSON_V1"},
                    )
                )
                data = result.model_dump(mode="python", exclude={"binding_digest"})
                data["scorer"] = scorer
                return EvaluationBinding.model_validate(
                    sealed_payload(
                        "EVALUATION_BINDING",
                        "binding_digest",
                        data,
                    )
                )
            return result

        def now(clock: SystemClock):
            return original_now(clock) + timedelta(
                seconds=60 if changed and change == "deadline" else 0
            )

        monkeypatch.setattr(PairedEvaluationService, "_checkpoint", checkpoint)
        monkeypatch.setattr(FrozenEvaluationCatalog, "resolve", resolve)
        monkeypatch.setattr(SystemClock, "now", now)
        result = await h.run()
        assert result["state"] == "HELD", result
        pair = result["pair"]
        assert pair["baseline"]["state"] == "SUCCEEDED"
        assert pair["candidate"] is None and pair["result"] is None
        expected = (
            "EVALUATION_BINDING_CHANGED" if change == "scorer" else "EVALUATION_DEADLINE_EXPIRED"
        )
        assert pair["reason_code"] == expected


async def test_reopen_resumes_durable_arm_without_repeating_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with pair_harness(tmp_path, BASE, CANDIDATE, CASES) as h:

        def interrupt(_service: PairedEvaluationService, label: str) -> None:
            if label == "after_baseline_receipt":
                raise RuntimeError("injected interruption")

        with monkeypatch.context() as patch:
            patch.setattr(PairedEvaluationService, "_checkpoint", interrupt)
            with pytest.raises(AssertionError):
                await h.run("first-interrupted")
        stored = SqliteEvaluationRunStore(h.runtime.ledger.engine).read_plan(
            h.project, h.plan["record_id"], h.plan["record_digest"]
        )
        assert stored is not None and stored.baseline is not None and stored.state == "PARTIAL"
        baseline_id = stored.baseline.execution_id
        h.runtime.close()
        reopened = create_runtime(
            tmp_path, evaluation_catalog=FrozenEvaluationCatalog((h.binding,))
        )
        try:
            h.runtime = reopened
            completed = await h.run("reopened-resume")
            assert completed["state"] == "COMPLETE", completed
            assert completed["pair"]["baseline"]["execution_id"] == baseline_id
        finally:
            reopened.close()


async def test_concurrent_pair_claim_executes_each_arm_once_without_transaction_during_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    original = IsolatedProgramExecutor.execute
    original_read = ContentAddressedObjectStore.read
    arms: list[str] = []

    async def execute(executor: IsolatedProgramExecutor, spec: Any, arm: Any, *args: Any):
        assert transaction._AMBIENT.get() is None  # pyright: ignore[reportPrivateUsage]
        arms.append(arm)
        if arm == "BASELINE":
            entered.set()
            await asyncio.wait_for(release.wait(), 5)
        return await original(executor, spec, arm, *args)

    def read(objects: ContentAddressedObjectStore, digest: str):
        assert transaction._AMBIENT.get() is None  # pyright: ignore[reportPrivateUsage]
        return original_read(objects, digest)

    async with pair_harness(tmp_path, BASE, CANDIDATE, CASES) as h:
        monkeypatch.setattr(IsolatedProgramExecutor, "execute", execute)
        monkeypatch.setattr(ContentAddressedObjectStore, "read", read)
        first = asyncio.create_task(h.run("concurrent-first"))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            second = await h.run("concurrent-second")
            assert second == {"state": "HELD", "reason_code": "EVALUATION_IN_PROGRESS_OR_TERMINAL"}
        finally:
            release.set()
        completed = await first
        assert completed["state"] == "COMPLETE", completed
        assert arms == ["BASELINE", "CANDIDATE"]


async def test_request_budget_is_rejected_before_process_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_process(*_args: Any, **_kwargs: Any):
        raise AssertionError("budget rejection must precede process creation")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_process)
    cases: list[dict[str, object]] = [
        *CASES,
        {**CASES[0], "public": {"case_id": "two", "payload": {}}},
    ]
    async with pair_harness(tmp_path, BASE, CANDIDATE, cases, max_requests=2) as h:
        result = await h.run()
        assert result == {"state": "HELD", "reason_code": "EVALUATION_REQUEST_BUDGET_EXHAUSTED"}


async def test_wrong_project_cannot_read_completed_pair(tmp_path: Path) -> None:
    async with pair_harness(tmp_path, BASE, CANDIDATE, CASES) as h:
        completed = await h.run()
        value(
            await h.runtime.bus.dispatch(
                request(
                    "project/create",
                    "other-project",
                    {
                        "project_id": "project:other",
                        "name": "Other",
                        "cutoff_at": "2026-09-01T00:00:00Z",
                    },
                )
            )
        )
        denied = await h.runtime.bus.dispatch(
            request(
                "improvement/evaluation/result/read",
                "wrong-read",
                {
                    "project_id": "project:other",
                    "pair_id": completed["pair"]["spec"]["pair_id"],
                },
            )
        )
        assert denied.error is not None and denied.error.message == "EVALUATION_RUN_NOT_FOUND"


@pytest.mark.parametrize("replace_scorer", [False, True])
async def test_new_plan_cannot_reset_case_pack_reuse_budget(
    tmp_path: Path, replace_scorer: bool
) -> None:
    async with pair_harness(tmp_path, BASE, CANDIDATE, CASES, max_pair_runs=1) as h:
        first = await h.run()
        assert first["state"] == "COMPLETE"
        old = h.plan["payload"]
        if replace_scorer:
            scorer = EvaluationScorer.model_validate(
                sealed_payload(
                    "EVALUATION_SCORER",
                    "scorer_digest",
                    {"scorer_id": "new-scorer", "version": "2", "algorithm": "EXACT_JSON_V1"},
                )
            )
            data = h.binding.model_dump(mode="python", exclude={"binding_digest"})
            data["scorer"] = scorer
            h.binding = EvaluationBinding.model_validate(
                sealed_payload(
                    "EVALUATION_BINDING",
                    "binding_digest",
                    data,
                )
            )
            h.runtime.close()
            h.runtime = create_runtime(
                tmp_path, evaluation_catalog=FrozenEvaluationCatalog((h.binding,))
            )
        h.plan = value(
            await h.runtime.bus.dispatch(
                request(
                    "improvement/evaluation/plan",
                    "second-plan",
                    {
                        "project_id": h.project,
                        "improvement_revision_id": h.proposal["record_id"],
                        "baseline_digest": old["baseline_digest"],
                        "datasets": old["datasets"],
                        "evaluator_refs": [h.binding.scorer.scorer_id],
                        "guardrails": [],
                        "exposure_policy_ref": "offline-only",
                    },
                )
            )
        )["evaluation_plan"]
        result = await h.run("second-plan-run")
        assert result == {"state": "HELD", "reason_code": "EVALUATION_EXPOSURE_REUSE_LIMIT"}


async def test_final_commit_fault_keeps_receipts_and_resumes_scoring_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with pair_harness(tmp_path, BASE, CANDIDATE, CASES) as h:

        def interrupt(_service: PairedEvaluationService, label: str) -> None:
            if label == "after_final_result":
                raise RuntimeError("injected precommit failure")

        with monkeypatch.context() as patch:
            patch.setattr(PairedEvaluationService, "_checkpoint", interrupt)
            with pytest.raises(AssertionError):
                await h.run("precommit-fault")
        stored = SqliteEvaluationRunStore(h.runtime.ledger.engine).read_plan(
            h.project, h.plan["record_id"], h.plan["record_digest"]
        )
        assert stored is not None and stored.state == "PARTIAL" and stored.result is None
        assert stored.baseline is not None and stored.candidate is not None

        async def no_execution(*_args: Any, **_kwargs: Any):
            raise AssertionError("both arm receipts were already committed")

        monkeypatch.setattr(IsolatedProgramExecutor, "execute", no_execution)
        completed = await h.run("score-resume")
        assert completed["state"] == "COMPLETE", completed
        assert completed["pair"]["baseline"]["execution_id"] == stored.baseline.execution_id
        assert completed["pair"]["candidate"]["execution_id"] == stored.candidate.execution_id


async def test_invalid_binding_does_not_expose_private_expected_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with pair_harness(tmp_path, BASE, CANDIDATE, CASES) as h:
        original = FrozenEvaluationCatalog.resolve

        def resolve(catalog: FrozenEvaluationCatalog, project: str, binding: str):
            result = original(catalog, project, binding)
            invalid = result.model_copy(update={"binding_digest": "f" * 64})
            return FrozenEvaluationCatalog((invalid,)).resolve(project, binding)

        monkeypatch.setattr(FrozenEvaluationCatalog, "resolve", resolve)
        result = await h.run()
        assert result == {"state": "HELD", "reason_code": "EVALUATION_BINDING_INVALID"}


async def test_revoked_access_preserves_held_pair_without_executing_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with pair_harness(tmp_path, BASE, CANDIDATE, CASES) as h:
        revoked = False
        original = ResourceScopeService.require_reads

        def checkpoint(_service: PairedEvaluationService, label: str) -> None:
            nonlocal revoked
            if label == "after_baseline_receipt":
                revoked = True

        def require_reads(service: ResourceScopeService, project: str, refs: tuple[str, ...]):
            if revoked and project == h.project:
                raise ResourceScopeError("RESOURCE_ACCESS_DENIED")
            return original(service, project, refs)

        monkeypatch.setattr(PairedEvaluationService, "_checkpoint", checkpoint)
        monkeypatch.setattr(ResourceScopeService, "require_reads", require_reads)
        with pytest.raises(AssertionError):
            await h.run("revoked-mid-pair")
        stored = SqliteEvaluationRunStore(h.runtime.ledger.engine).read_plan(
            h.project, h.plan["record_id"], h.plan["record_digest"]
        )
        assert stored is not None and stored.state == "HELD"
        assert stored.reason_code == "RESOURCE_ACCESS_DENIED"
        assert stored.baseline is not None and stored.candidate is None and stored.result is None
