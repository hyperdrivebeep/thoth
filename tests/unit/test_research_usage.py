from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from thoth.application.services.research_progress_view import progress_view
from thoth.application.services.research_usage import (
    operation_wall_ms,
    summarize_operation_usage,
    summarize_usage,
    usage_label,
)
from thoth.domain.account_usage import AccountQuotaSnapshot
from thoth.domain.model_dispatch import CONTROLLED_MODEL_CONTROL, ModelDispatchRecord


def dispatch(
    identifier: str, inputs: int | None, outputs: int | None, thread: str | None = "t"
) -> ModelDispatchRecord:
    return ModelDispatchRecord(
        dispatch_id=identifier,
        thread_id=thread,
        capability=CONTROLLED_MODEL_CONTROL,
        payload_bytes=1000,
        payload_digest="a" * 64,
        output_reserved=5000,
        input_tokens=inputs,
        output_tokens=outputs,
    )


def test_usage_is_deduplicated_by_dispatch_and_excludes_other_threads() -> None:
    first = dispatch("first", 12, 8)
    result = summarize_usage(
        [first, first, dispatch("repair", 7, 3), dispatch("other", 900, 900, "q")], "t", 2
    )
    assert (result["input_tokens"], result["output_tokens"], result["total_tokens"]) == (19, 11, 30)
    assert result["state"] == "OBSERVED" and result["unreported_calls"] == 0


def test_missing_or_legacy_usage_is_not_zero_or_a_reservation_estimate() -> None:
    result = summarize_usage(
        [dispatch("legacy", 100, 200, None), dispatch("new", None, None)], "t", 2
    )
    assert result["state"] == "UNKNOWN" and result["total_tokens"] is None
    assert result["unreported_calls"] == 2
    partial = summarize_usage([dispatch("one", 12, None)], "t", 2)
    assert partial["state"] == "PARTIAL" and partial["input_tokens"] == 12
    assert partial["output_tokens"] is None and partial["total_tokens"] == 12


def test_explicit_zero_usage_is_a_reported_value() -> None:
    result = summarize_usage([dispatch("zero", 0, 0)], "t", 1)
    assert result["total_tokens"] == 0 and result["state"] == "OBSERVED"


def test_tui_consumes_shared_usage_without_inventing_a_cost() -> None:
    usage = summarize_usage([dispatch("partial", 12, None)], "t", 1)
    status = progress_view({"usage": usage})
    assert status["usage"] == usage
    assert status["usage_summary"] == "사용 토큰 12 (부분 관측) · 추정 비용 미확인"
    assert usage["estimated_cost"] is None and usage["cost_basis"] is None
    assert usage_label(None) == "사용 토큰 미확인 · 추정 비용 미확인"


def test_cached_tokens_are_not_added_again_to_total() -> None:
    record = dispatch("cached", 12, 3)
    record = record.model_copy(update={"cached_input_tokens": 8})
    result = summarize_usage([record], "t", 4)
    assert result["input_tokens"] == 12 and result["total_tokens"] == 15
    assert result["cached_input_tokens"] == 8
    assert result["cached_tokens_state"] == "OBSERVED"
    assert result["model_transports"] == 1
    assert result["non_model_reservations"] == 3


def test_account_quota_keeps_json_shape_and_rejects_opaque_values() -> None:
    observed = AccountQuotaSnapshot(
        state="OBSERVED", remaining_percent=60, provider="codex-oauth"
    ).model_dump(mode="json")
    result = summarize_usage([], "t", 0, account_quota=observed)
    assert result["account_quota"] == observed
    with pytest.raises(ValidationError):
        summarize_usage([], "t", 0, account_quota={"opaque": object()})


def op_dispatch(
    identifier: str,
    operation: str | None,
    inputs: int | None,
    outputs: int | None,
    cached: int | None = None,
) -> ModelDispatchRecord:
    return dispatch(identifier, inputs, outputs).model_copy(
        update={"operation_id": operation, "cached_input_tokens": cached}
    )


def test_each_result_gets_only_its_own_operations_calls() -> None:
    records = [
        op_dispatch("a1", "op-1", 10, 5, 2),
        op_dispatch("a2", "op-1", 20, 5),
        op_dispatch("b1", "op-2", 100, 50, 40),
        dispatch("other-thread", 900, 900, "q").model_copy(update={"operation_id": "op-1"}),
    ]
    first = summarize_operation_usage(records, "t", "op-1")
    second = summarize_operation_usage(records, "t", "op-2")
    assert first is not None and second is not None
    assert (first["calls"], first["input_tokens"], first["output_tokens"]) == (2, 30, 10)
    assert (first["total_tokens"], first["cached_input_tokens"], first["state"]) == (
        40,
        2,
        "OBSERVED",
    )
    assert (second["calls"], second["total_tokens"], second["cached_input_tokens"]) == (1, 150, 40)
    assert first["operation_id"] == "op-1" and second["operation_id"] == "op-2"


def test_a_call_without_reported_tokens_makes_the_result_partial_not_zero() -> None:
    result = summarize_operation_usage(
        [op_dispatch("a", "op-1", 12, None), op_dispatch("b", "op-1", 3, 4)], "t", "op-1"
    )
    assert result is not None
    assert (result["state"], result["unreported_calls"], result["calls"]) == ("PARTIAL", 1, 2)
    assert result["total_tokens"] == 19
    nothing = summarize_operation_usage([op_dispatch("a", "op-1", None, None)], "t", "op-1")
    assert nothing is not None
    assert (nothing["state"], nothing["total_tokens"], nothing["unreported_calls"]) == (
        "UNKNOWN",
        None,
        1,
    )


def test_a_dispatch_is_counted_once_and_unattributed_ones_belong_to_no_result() -> None:
    same = op_dispatch("same", "op-1", 5, 5)
    legacy = op_dispatch("legacy", None, 700, 700)
    result = summarize_operation_usage([same, same, legacy], "t", "op-1")
    assert result is not None and (result["calls"], result["total_tokens"]) == (1, 10)
    assert summarize_operation_usage([legacy], "t", "op-1") is None
    assert summarize_operation_usage([], "t", "op-1") is None


def test_wall_time_is_the_recorded_start_to_end_and_unknown_otherwise() -> None:
    start = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    assert operation_wall_ms(start, start + timedelta(minutes=7, seconds=30)) == 450_000
    assert operation_wall_ms(start, None) is None
    assert operation_wall_ms(start, start - timedelta(seconds=1)) is None
    with_wall = summarize_operation_usage(
        [op_dispatch("a", "op-1", 1, 1)], "t", "op-1", wall_ms=450_000
    )
    assert with_wall is not None and with_wall["wall_ms"] == 450_000
    without = summarize_operation_usage([op_dispatch("a", "op-1", 1, 1)], "t", "op-1")
    assert without is not None and without["wall_ms"] is None
