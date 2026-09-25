"""Measured output, isolation, partial resume and tamper gates through public RPC."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import update
from tests.integration.paired_evaluation_helpers import pair_harness
from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.storage import ContentAddressedObjectStore
from thoth.adapters.storage.evaluation_run import SqliteEvaluationRunStore
from thoth.adapters.storage.evaluation_run_schema import evaluation_runs
from thoth.application.services.paired_evaluation_service import PairedEvaluationService


@pytest.mark.parametrize(
    "candidate,expected,verdict",
    [
        ({"op": "input", "key": "x"}, 2, "NO_MATERIAL_CHANGE"),
        ({"op": "literal", "value": 0}, 2, "WORSE"),
        (
            {"op": "literal", "value": {"official_kpi_changed": True}},
            {"official_kpi_changed": True},
            "WORSE",
        ),
    ],
)
async def test_actual_outputs_keep_equal_worse_and_hard_failure_distinct(
    tmp_path: Path,
    candidate: dict[str, object],
    expected: object,
    verdict: str,
) -> None:
    cases: list[dict[str, object]] = [
        {
            "public": {"case_id": "one", "payload": {"x": 2}},
            "expected_output": {"answer": expected},
            "axis": "quality",
        }
    ]
    async with pair_harness(tmp_path, {"op": "input", "key": "x"}, candidate, cases) as h:
        response = await h.run()
        assert response["state"] == "COMPLETE", response
        assert response["pair"]["result"]["performance_verdict"] == verdict
        assert response["pair"]["result"]["promotion_eligible"] is False


async def test_case_regression_is_mixed_even_when_other_cases_improve(tmp_path: Path) -> None:
    candidate = {
        "op": "subtract",
        "args": [{"op": "input", "key": "x"}, {"op": "literal", "value": 2}],
    }
    cases: list[dict[str, object]] = [
        {
            "public": {"case_id": "one", "payload": {"x": 3}},
            "expected_output": {"answer": 3},
            "axis": "quality",
        },
        {
            "public": {"case_id": "two", "payload": {"x": 4}},
            "expected_output": {"answer": 2},
            "axis": "quality",
        },
    ]
    async with pair_harness(tmp_path, {"op": "input", "key": "x"}, candidate, cases) as h:
        response = await h.run()
        assert response["pair"]["result"]["performance_verdict"] == "MIXED"


async def test_candidate_does_not_receive_baseline_memory_mutations(tmp_path: Path) -> None:
    cases: list[dict[str, object]] = [
        {
            "public": {"case_id": "one", "payload": {}},
            "expected_output": {"answer": None},
            "axis": "privacy",
        }
    ]
    async with pair_harness(
        tmp_path,
        {"op": "literal", "value": 1},
        {"op": "memory", "key": "baseline_private"},
        cases,
        baseline_memory_updates={
            "baseline_private": {"op": "literal", "value": "baseline-only-marker"}
        },
    ) as h:
        response = await h.run()
        result = response["pair"]["result"]
        observed = json.loads(
            ContentAddressedObjectStore(tmp_path).read(result["candidate"]["output_blob_digest"])
        )
        assert observed["outputs"] == [{"answer": None}]
        assert "baseline-only-marker" not in str(observed)
        assert (
            result["baseline"]["initial_memory_digest"]
            == result["candidate"]["initial_memory_digest"]
        )
        assert (
            result["baseline"]["final_memory_digest"] != result["candidate"]["final_memory_digest"]
        )


async def test_holdout_and_parent_credentials_never_enter_worker_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[bytes] = []
    environments: list[dict[str, str]] = []
    original_write = asyncio.StreamWriter.write
    original_spawn = asyncio.create_subprocess_exec
    monkeypatch.setenv("N05_PARENT_SECRET", "parent-secret-marker")

    def capture_write(writer: asyncio.StreamWriter, data: bytes) -> None:
        captured.append(data)
        original_write(writer, data)

    async def capture_spawn(*args: Any, **kwargs: Any):
        environments.append(dict(kwargs["env"]))
        return await original_spawn(*args, **kwargs)

    monkeypatch.setattr(asyncio.StreamWriter, "write", capture_write)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture_spawn)
    cases: list[dict[str, object]] = [
        {
            "public": {"case_id": "one", "payload": {}},
            "expected_output": {"answer": "scorer-only-hidden-marker"},
            "axis": "quality",
        }
    ]
    async with pair_harness(
        tmp_path, {"op": "literal", "value": 0}, {"op": "literal", "value": 1}, cases
    ) as h:
        response = await h.run()
        assert response["state"] == "COMPLETE", response
    assert len(captured) == 2 and len(environments) == 2
    assert all(
        b"scorer-only-hidden-marker" not in data and b"expected_output" not in data
        for data in captured
    )
    assert all("N05_PARENT_SECRET" not in environment for environment in environments)


async def test_partial_pair_resumes_only_the_unstarted_arm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases: list[dict[str, object]] = [
        {
            "public": {"case_id": "one", "payload": {"x": 2}},
            "expected_output": {"answer": 3},
            "axis": "quality",
        }
    ]
    async with pair_harness(
        tmp_path, {"op": "literal", "value": 2}, {"op": "literal", "value": 3}, cases
    ) as h:

        def fail_after_baseline(service: PairedEvaluationService, label: str) -> None:
            del service
            if label == "after_baseline_receipt":
                raise RuntimeError("injected after durable baseline receipt")

        with monkeypatch.context() as patch:
            patch.setattr(PairedEvaluationService, "_checkpoint", fail_after_baseline)
            failed = await h.runtime.bus.dispatch(
                request(
                    "improvement/evaluation/run",
                    "partial-first",
                    {
                        "project_id": h.project,
                        "evaluation_plan_id": h.plan["record_id"],
                        "expected_evaluation_revision": 1,
                        "binding_id": h.binding.binding_id,
                    },
                )
            )
            assert failed.error is not None
        stored = SqliteEvaluationRunStore(h.runtime.ledger.engine).read_plan(
            h.project, h.plan["record_id"], h.plan["record_digest"]
        )
        assert stored is not None and stored.state == "PARTIAL" and stored.baseline is not None
        original_id = stored.baseline.execution_id
        resumed = await h.run("partial-resume")
        assert resumed["state"] == "COMPLETE", resumed
        assert resumed["pair"]["baseline"]["execution_id"] == original_id
        assert resumed["pair"]["candidate"]["execution_id"] != original_id


async def test_fake_assessment_reference_and_corrupt_projection_are_not_evidence(
    tmp_path: Path,
) -> None:
    cases: list[dict[str, object]] = [
        {
            "public": {"case_id": "one", "payload": {}},
            "expected_output": {"answer": 1},
            "axis": "quality",
        }
    ]
    async with pair_harness(
        tmp_path, {"op": "literal", "value": 0}, {"op": "literal", "value": 1}, cases
    ) as h:
        completed = await h.run()
        pair = completed["pair"]
        result = pair["result"]
        fake = value(
            await h.runtime.bus.dispatch(
                request(
                    "improvement/evaluation/assess",
                    "fake-assessment",
                    {
                        "project_id": h.project,
                        "evaluation_plan_id": h.plan["record_id"],
                        "expected_evaluation_revision": 1,
                        "result_refs": [result["pair_id"]],
                        "evaluator_result_refs": ["f" * 64],
                        "exposure_ledger_ref": result["exposure_id"],
                    },
                )
            )
        )
        assert fake["evaluation_validity"] == "INCONCLUSIVE"
        assert fake["promotion_eligibility"] is False
        with h.runtime.ledger.engine.begin() as connection:
            connection.execute(
                update(evaluation_runs)
                .where(evaluation_runs.c.pair_id == result["pair_id"])
                .values(revision=999)
            )
        denied = await h.runtime.bus.dispatch(
            request(
                "improvement/evaluation/result/read",
                "tampered-read",
                {
                    "project_id": h.project,
                    "pair_id": result["pair_id"],
                },
            )
        )
        assert denied.error is not None and denied.error.message == "EVALUATION_PROJECTION_MISMATCH"


async def test_exposed_holdout_is_held_before_any_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_process(*_args: Any, **_kwargs: Any):
        raise AssertionError("exposed holdout must not reach executor")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_process)
    cases: list[dict[str, object]] = [
        {
            "public": {"case_id": "one", "payload": {}},
            "expected_output": {"answer": 1},
            "axis": "quality",
        }
    ]
    async with pair_harness(
        tmp_path,
        {"op": "literal", "value": 0},
        {"op": "literal", "value": 1},
        cases,
        expected_values_exposed=True,
    ) as h:
        held = await h.run()
        assert held == {"state": "HELD", "reason_code": "EVALUATION_HOLDOUT_EXPOSED"}
