from typing import cast

from pydantic import JsonValue

from thoth.application.services.research_progress_view import (
    progress_view,
    project_activity_events,
)


def _event_record(event: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(event, dict)
    return event


def _payload(event: JsonValue) -> dict[str, JsonValue]:
    record = _event_record(event)
    payload = record["payload"]
    assert isinstance(payload, dict)
    return payload


def test_activity_events_keep_stage_order_and_concrete_payloads() -> None:
    events = project_activity_events(
        {
            "completed_stages": [
                {
                    "role": "RESEARCH_PLANNER",
                    "state": "COMPLETED",
                    "elapsed_ms": 91700,
                    "context_bytes": 12000,
                    "dispatch_ids": ["d1"],
                },
                {
                    "role": "EVIDENCE_RERANKER",
                    "state": "COMPLETED",
                    "elapsed_ms": 59100,
                    "context_bytes": 8000,
                    "dispatch_ids": ["d2"],
                },
            ],
            "attempt": {
                "phase": "HYPOTHESIS_REVIEW",
                "draft_progress": {
                    "evidence_focus": {"locators": [{"page": 6, "exact_text": "70.6"}]},
                    "portfolio": {"hypotheses": [{}, {}, {}, {}]},
                    "hypothesis_review": {"decisions": [{}, {}]},
                },
            },
            "model_dispatches": [
                {
                    "state": "RESERVED",
                    "transport_observation": {"elapsed_ms": 47000, "received_bytes": 0},
                }
            ],
        }
    )
    kinds = [_event_record(event)["kind"] for event in events]
    assert kinds == [
        "note",
        "action",
        "note",
        "action",
        "action",
        "note",
        "action",
        "action",
    ]
    assert _event_record(events[0])["key"] == "RESEARCH_PLANNER"
    assert _event_record(events[1])["action_type"] == "stage_completed"
    assert _payload(events[1])["elapsed_ms"] == 91700
    assert _event_record(events[2])["key"] == "EVIDENCE_RERANKER"
    assert _event_record(events[4])["action_type"] == "source_read"
    assert _payload(events[4])["page"] == 6
    assert _event_record(events[5])["key"] == "HYPOTHESIS_REVIEW"
    assert _event_record(events[5])["live"] is True
    assert _payload(events[6])["hypothesis_count"] == 4
    assert _event_record(events[7])["action_type"] == "model_call"
    assert _event_record(events[7])["live"] is True


def test_progress_view_exposes_activity_events_without_replacing_stages() -> None:
    stages: list[JsonValue] = [{"role": "RESEARCH_PLANNER", "elapsed_ms": 10, "context_bytes": 4}]
    status = progress_view({"completed_stages": stages, "attempt": {"phase": "REQUIREMENTS"}})
    assert status["completed_stages"] == stages
    events = status["activity_events"]
    assert isinstance(events, list)
    assert _event_record(cast(JsonValue, events[0]))["key"] == "RESEARCH_PLANNER"
    assert _event_record(cast(JsonValue, events[-1]))["key"] == "REQUIREMENTS"
