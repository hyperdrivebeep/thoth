from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from tests.integration.test_a02_autonomous_acquisition import request, value
from tests.integration.test_a05_bounded_recovery import (
    RecoverySandboxAdapter,
    prepare_recovery,
)

from thoth.adapters.improvement import DeterministicIndependentImprovementEvaluator
from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.domain.improvement import ImprovementEvaluation
from thoth.domain.sandbox import SandboxExecutionState


class RaisingEvaluator:
    def evaluate(
        self,
        *,
        baseline_digest: str,
        candidate_digest: str,
        fixture_digest: str,
        hidden_holdout_digest: str,
        max_requests: int,
        timeout_seconds: int,
    ) -> ImprovementEvaluation:
        del (
            baseline_digest,
            candidate_digest,
            fixture_digest,
            hidden_holdout_digest,
            max_requests,
            timeout_seconds,
        )
        raise RuntimeError("injected independent evaluator failure")


@pytest.mark.asyncio
async def test_explicit_test_evaluator_simulates_management_canary(
    tmp_path: Path,
) -> None:
    adapter = RecoverySandboxAdapter(
        (
            (SandboxExecutionState.FAILED, "SEMANTIC evaluator mismatch"),
            (SandboxExecutionState.FAILED, "SEMANTIC evaluator mismatch"),
        )
    )
    runtime, project_id, thread_id = await prepare_recovery(
        tmp_path,
        "a08-repeated-semantic",
        adapter,
        max_retries=0,
        improvement_evaluator=DeterministicIndependentImprovementEvaluator(),
    )
    try:
        first = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "a08-first-failure",
                    {"project_id": project_id, "thread_id": thread_id},
                )
            )
        )
        first_improvement = cast(dict[str, JsonValue], first["recursive_improvement"])
        assert first_improvement["state"] == "BELOW_THRESHOLD"
        assert first_improvement["failure_count"] == 1

        second = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "a08-second-failure",
                    {"project_id": project_id, "thread_id": thread_id},
                )
            )
        )
        improvement = cast(dict[str, JsonValue], second["recursive_improvement"])
        assert improvement["state"] == "PROMOTED_LOCAL"
        assert improvement["failure_count"] == 2
        assert improvement["baseline_digest"] != improvement["candidate_digest"]
        assert improvement["fixture_digest"]
        assert improvement["evaluator_id"] == "DETERMINISTIC_INDEPENDENT_EVALUATOR_V1"
        assert improvement["hidden_holdout_exposed"] is False
        assert improvement["exact_digest_canary"] is True
        exposures = cast(list[dict[str, JsonValue]], improvement["exposures"])
        assert [item["stage"] for item in exposures] == [
            "OFFLINE",
            "SANDBOX",
            "SHADOW",
            "CANARY",
        ]
        assert all(
            item["candidate_digest"] == improvement["candidate_digest"] for item in exposures
        )
        assert improvement["official_kpi_changed"] is False
        assert improvement["safety_threshold_changed"] is False
        assert improvement["waiver_changed"] is False
        assert improvement["r4_decision_changed"] is False
        assert improvement["model_weights_changed"] is False
    finally:
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "reason"),
    (
        ({"candidate_quality_bps": 5_000}, "NO_IMPROVEMENT"),
        ({"critical_regression": True}, "CRITICAL_REGRESSION"),
        ({"hidden_holdout_exposed": True}, "HOLDOUT_LEAK"),
        ({"budget_exhausted": True}, "BUDGET_EXHAUSTED"),
        ({"timed_out": True}, "TIMEOUT"),
        ({"candidate_cost_microunits": 1_100_000}, "COST_REGRESSION"),
    ),
)
async def test_invalid_evaluation_outcomes_automatically_restore_immutable_baseline(
    tmp_path: Path,
    outcome: dict[str, object],
    reason: str,
) -> None:
    scenario = reason.casefold().replace("_", "-")
    runtime, project_id, thread_id = await prepare_recovery(
        tmp_path,
        f"a08-{scenario}",
        RecoverySandboxAdapter(
            (
                (SandboxExecutionState.FAILED, "SEMANTIC repeated failure"),
                (SandboxExecutionState.FAILED, "SEMANTIC repeated failure"),
            )
        ),
        max_retries=0,
        improvement_evaluator=DeterministicIndependentImprovementEvaluator(outcome),
    )
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    f"a08-{scenario}-first",
                    {"project_id": project_id, "thread_id": thread_id},
                )
            )
        )
        second = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    f"a08-{scenario}-second",
                    {"project_id": project_id, "thread_id": thread_id},
                )
            )
        )
        result = cast(dict[str, JsonValue], second["recursive_improvement"])
        assert result["state"] == "ROLLED_BACK"
        assert result["rollback_reason"] == reason
        assert result["active_digest"] == result["baseline_digest"]
        assert result["candidate_digest"] != result["baseline_digest"]
        exposures = cast(list[dict[str, JsonValue]], result["exposures"])
        assert [item["stage"] for item in exposures] == ["OFFLINE"]
        assert result["exact_digest_canary"] is False
        assert result["hidden_holdout_exposed"] is (reason == "HOLDOUT_LEAK")
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_failed_candidate_digest_is_not_retried_on_third_identical_failure(
    tmp_path: Path,
) -> None:
    runtime, project_id, thread_id = await prepare_recovery(
        tmp_path,
        "a08-same-digest",
        RecoverySandboxAdapter(
            (
                (SandboxExecutionState.FAILED, "SEMANTIC same failure"),
                (SandboxExecutionState.FAILED, "SEMANTIC same failure"),
                (SandboxExecutionState.FAILED, "SEMANTIC same failure"),
            )
        ),
        max_retries=0,
        improvement_evaluator=DeterministicIndependentImprovementEvaluator(
            {"candidate_quality_bps": 5_000}
        ),
    )
    try:
        for ordinal in (1, 2):
            value(
                await runtime.bus.dispatch(
                    request(
                        "thread/input",
                        f"a08-same-digest-{ordinal}",
                        {"project_id": project_id, "thread_id": thread_id},
                    )
                )
            )
        third = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "a08-same-digest-3",
                    {"project_id": project_id, "thread_id": thread_id},
                )
            )
        )
        result = cast(dict[str, JsonValue], third["recursive_improvement"])
        assert result["state"] == "REJECTED_SAME_DIGEST"
        assert result["rollback_reason"] == "SAME_DIGEST_RETRY_PROHIBITED"
        assert result["active_digest"] == result["baseline_digest"]
        assert result["exposures"] == []
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_improvement_failure_preserves_request_without_promoting_candidate(
    tmp_path: Path,
) -> None:
    runtime, project_id, thread_id = await prepare_recovery(
        tmp_path,
        "a08-uow-failure",
        RecoverySandboxAdapter(
            (
                (SandboxExecutionState.FAILED, "SEMANTIC evaluator crash"),
                (SandboxExecutionState.FAILED, "SEMANTIC evaluator crash"),
            )
        ),
        max_retries=0,
        improvement_evaluator=RaisingEvaluator(),
    )
    records = SqliteControlRecordStore(runtime.ledger.engine)
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "a08-uow-first",
                    {"project_id": project_id, "thread_id": thread_id},
                )
            )
        )
        before = records.list(
            project_id,
            "IMPROVEMENT_RUNTIME",
            "FAILURE_OBSERVATION",
            latest_only=False,
        )
        failed = await runtime.bus.dispatch(
            request(
                "thread/input",
                "a08-uow-second",
                {"project_id": project_id, "thread_id": thread_id},
            )
        )
        assert failed.error is not None
        assert failed.error.message == "internal command failure"
        after = records.list(
            project_id,
            "IMPROVEMENT_RUNTIME",
            "FAILURE_OBSERVATION",
            latest_only=False,
        )
        assert len(before) == 1 and len(after) == 2
        assert after[0] == before[0]
        requests = records.list(project_id, "IMPROVEMENT_RUNTIME", "EVALUATION_REQUEST")
        assert len(requests) == 1 and requests[0].state == "FAILED"
        assert records.list(project_id, "IMPROVEMENT_RUNTIME", "RUN") == ()
    finally:
        runtime.close()
