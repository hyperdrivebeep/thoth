"""Observed dispatch usage, separate from reservation estimates and account billing."""

from collections.abc import Iterable
from typing import cast

from pydantic import JsonValue, TypeAdapter

from thoth.domain.model_dispatch import ModelDispatchRecord

_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


def summarize_usage(
    records: Iterable[ModelDispatchRecord],
    thread: str,
    calls: int,
    *,
    account_quota: object | None = None,
) -> dict[str, JsonValue]:
    # The caller supplies latest journal versions; dispatch identity deduplicates rereads.
    dispatches = {r.dispatch_id: r for r in records if r.thread_id == thread}
    inputs = [
        r.input_tokens
        for r in dispatches.values()
        if r.input_tokens is not None and r.input_tokens >= 0
    ]
    outputs = [
        r.output_tokens
        for r in dispatches.values()
        if r.output_tokens is not None and r.output_tokens >= 0
    ]
    complete = sum(
        r.input_tokens is not None
        and r.input_tokens >= 0
        and r.output_tokens is not None
        and r.output_tokens >= 0
        for r in dispatches.values()
    )
    unknown = max(0, len(dispatches) - complete)
    unknown = max(unknown, max(0, calls - complete))
    cached = [
        r.cached_input_tokens
        for r in dispatches.values()
        if r.cached_input_tokens is not None and r.cached_input_tokens >= 0
    ]
    quota: dict[str, JsonValue]
    if isinstance(account_quota, dict):
        quota = _JSON_OBJECT.validate_python(account_quota)
    else:
        quota = {"state": "UNKNOWN", "remaining_percent": None, "observed_at": None}
    known = bool(inputs or outputs)
    return {
        "schema_version": "1.0.0",
        "input_tokens": sum(inputs) if inputs else None,
        "output_tokens": sum(outputs) if outputs else None,
        "total_tokens": sum(inputs) + sum(outputs) if known else None,
        "unreported_calls": unknown,
        "model_transports": len(dispatches),
        "non_model_reservations": max(0, calls - len(dispatches)),
        "state": "UNKNOWN" if not known else "PARTIAL" if unknown else "OBSERVED",
        "total_basis": "REPORTED_INPUT_PLUS_OUTPUT",
        "cumulative_token_limit_enforced": False,
        "total_time_limit_enforced": False,
        "total_call_limit_enforced": False,
        "account_quota": quota,
        "cached_input_tokens": sum(cached) if cached else None,
        "cached_tokens_state": "OBSERVED" if cached else "UNKNOWN",
        # Current transports supply usage, but no applicable pricing/cost observation.
        # Absence is not zero and must not trigger another provider call.
        "estimated_cost": None,
        "cost_state": "UNKNOWN",
        "cost_basis": None,
    }


def usage_label(usage: object) -> str:
    observed = cast(dict[str, object], usage) if isinstance(usage, dict) else {}
    total = observed.get("total_tokens")
    tokens = f"{total:,}" if isinstance(total, int) and not isinstance(total, bool) else "미확인"
    if observed.get("state") == "PARTIAL":
        tokens += " (부분 관측)"
    return f"사용 토큰 {tokens} · 추정 비용 미확인"
