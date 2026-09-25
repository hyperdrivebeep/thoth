import pytest
from pydantic import ValidationError

from thoth.application.services.research_progress_view import progress_view
from thoth.application.services.research_usage import summarize_usage, usage_label
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
