"""Small user-facing progress view; full provenance remains available through /status."""

from typing import cast

from pydantic import JsonValue

from thoth.application.services.research_usage import usage_label
from thoth.application.services.user_activity_projection import project_user_activity_events
from thoth.domain.evidence import EvidenceSpan

EVIDENCE_FOCUS_LIMIT = 12


def evidence_focus_payload(
    spans: tuple[EvidenceSpan, ...], *, limit: int = EVIDENCE_FOCUS_LIMIT
) -> dict[str, object]:
    chosen = spans[:limit]
    return {
        "span_ids": [span.span_id for span in chosen],
        "artifact_ids": sorted({span.artifact_id for span in chosen}),
        "locators": [
            {
                "span_id": span.span_id,
                "page": span.locator.page,
                "exact_text": span.exact_text[:160],
            }
            for span in chosen
        ],
        "total": len(spans),
        "truncated": len(spans) > limit,
    }


def merge_draft_progress(
    previous: dict[str, object], content: dict[str, object]
) -> dict[str, object]:
    merged = dict(content)
    focus = previous.get("evidence_focus")
    if focus is not None and "evidence_focus" not in merged:
        merged["evidence_focus"] = focus
    return merged


def _as_record(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def _json_atom(value: object) -> JsonValue:
    if value is None or isinstance(value, str | float | bool | int):
        return value
    return None


def project_activity_events(value: dict[str, JsonValue]) -> list[JsonValue]:
    """Read projection: chronological note/action pairs from already-published stage facts."""
    events: list[JsonValue] = []
    seq = 0

    def add(event: dict[str, object]) -> None:
        nonlocal seq
        seq += 1
        events.append(cast(JsonValue, {"seq": seq, **event}))

    for item in _as_list(value.get("completed_stages")):
        stage = _as_record(item)
        role = stage.get("role")
        dispatches = stage.get("dispatch_ids")
        add({"kind": "note", "key": role if isinstance(role, str) else None, "live": False})
        add(
            {
                "kind": "action",
                "action_type": "stage_completed",
                "live": False,
                "payload": {
                    "elapsed_ms": _json_atom(stage.get("elapsed_ms")),
                    "context_bytes": _json_atom(stage.get("context_bytes")),
                    "dispatch_count": (
                        len(cast(list[object], dispatches))
                        if isinstance(dispatches, list)
                        else None
                    ),
                    "state": _json_atom(stage.get("state")),
                },
            }
        )

    attempt = _as_record(value.get("attempt"))
    draft = _as_record(attempt.get("draft_progress"))
    focus = _as_record(draft.get("evidence_focus"))
    for item in _as_list(focus.get("locators")):
        locator = _as_record(item)
        add(
            {
                "kind": "action",
                "action_type": "source_read",
                "live": False,
                "payload": {
                    "page": _json_atom(locator.get("page")),
                    "exact_text": _json_atom(locator.get("exact_text")),
                    "span_id": _json_atom(locator.get("span_id")),
                },
            }
        )

    phase = attempt.get("phase")
    if isinstance(phase, str) and phase:
        add({"kind": "note", "key": phase, "live": True})

    portfolio = _as_record(draft.get("portfolio"))
    hypotheses = _as_list(portfolio.get("hypotheses"))
    review = _as_record(draft.get("hypothesis_review"))
    decisions = _as_list(review.get("decisions"))
    if hypotheses:
        add(
            {
                "kind": "action",
                "action_type": "hypothesis_review",
                "live": len(decisions) < len(hypotheses),
                "payload": {
                    "hypothesis_count": len(hypotheses),
                    "review_count": len(decisions) if decisions else None,
                },
            }
        )

    active: dict[str, object] | None = None
    dispatch_records = _as_list(value.get("model_dispatches"))
    for item in reversed(dispatch_records):
        candidate = _as_record(item)
        if candidate.get("state") == "RESERVED":
            active = candidate
            break
    if active is None and dispatch_records:
        active = _as_record(dispatch_records[-1])
    if active is not None:
        observation = _as_record(active.get("transport_observation"))
        add(
            {
                "kind": "action",
                "action_type": "model_call",
                "live": active.get("state") == "RESERVED",
                "payload": {
                    "state": _json_atom(active.get("state")),
                    "elapsed_ms": _json_atom(observation.get("elapsed_ms")),
                    "first_byte_ms": _json_atom(observation.get("first_byte_ms"))
                    or _json_atom(observation.get("first_response_ms")),
                    "received_bytes": _json_atom(observation.get("received_bytes"))
                    or _json_atom(active.get("received_bytes")),
                },
            }
        )
    return events


def progress_view(value: dict[str, JsonValue]) -> dict[str, object]:
    current = value.get("current_result")
    current = current if isinstance(current, dict) else {}
    result = current.get("result")
    result = result if isinstance(result, dict) else {}
    attempt = value.get("attempt")
    attempt = attempt if isinstance(attempt, dict) else {}
    draft = attempt.get("draft_progress")
    draft = draft if isinstance(draft, dict) else {}
    portfolio = draft.get("portfolio")
    portfolio = portfolio if isinstance(portfolio, dict) else {}
    hypotheses = portfolio.get("hypotheses")
    usage = value.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    quota = usage.get("account_quota")
    quota = quota if isinstance(quota, dict) else {}
    dispatches = value.get("model_dispatches")
    rejection = None
    if isinstance(dispatches, list):
        for item in dispatches:
            if not isinstance(item, dict):
                continue
            observation = item.get("transport_observation")
            observation = observation if isinstance(observation, dict) else {}
            candidate = observation.get("http_rejection")
            if isinstance(candidate, dict):
                rejection = candidate
    resume = value.get("resume_information")
    resume = resume if isinstance(resume, dict) else {}
    return {
        "project_id": value.get("project_id"),
        "thread_id": value.get("thread_id"),
        "request_epoch": value.get("request_epoch"),
        "phase": attempt.get("phase"),
        "operation_state": value.get("operation_state"),
        "error": value.get("operation_error"),
        "answer": result.get("answer"),
        "answer_status": result.get("answer_status"),
        "gaps": current.get("gaps"),
        "next_steps": current.get("next_steps"),
        "terminal_reason": current.get("terminal_reason"),
        "usage": value.get("usage"),
        "usage_summary": usage_label(value.get("usage")),
        "http_rejection": rejection,
        "account_quota": quota,
        "provider_acceptance_unknown": resume.get("provider_acceptance_unknown", True),
        "failure": value.get("failure"),
        "execution_summary": value.get("execution_summary"),
        "cleanup_failure": value.get("cleanup_failure"),
        "answer_outcome": value.get("answer_outcome"),
        "resume_information": value.get("resume_information"),
        "completed_stages": value.get("completed_stages"),
        "activity_events": project_activity_events(value),
        "user_activity_events": project_user_activity_events(value),
        "draft_state": draft.get("state"),
        "proposed_hypotheses": len(cast(list[object], hypotheses))
        if isinstance(hypotheses, list)
        else None,
    }
