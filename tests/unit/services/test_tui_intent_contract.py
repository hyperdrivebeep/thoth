from __future__ import annotations

from datetime import UTC, datetime

from thoth.application.services.conversation_router import ConversationRouter
from thoth.domain.conversation import ConversationIntent, TuiSessionState


def session(project_id: str | None = None, thread_id: str | None = None) -> TuiSessionState:
    return TuiSessionState(
        session_id="tui:test",
        active_project_id=project_id,
        active_thread_id=thread_id,
        revision=0,
        updated_at=datetime(2026, 9, 1, tzinfo=UTC),
    )


def test_free_text_routes_to_start_or_continue_without_becoming_canonical_state() -> None:
    router = ConversationRouter()
    start = router.route("Investigate the latency regression", session("project:tui"))
    assert start.intent == ConversationIntent.START_THREAD
    assert start.method == "thread/start"
    assert start.arguments["problem"] == "Investigate the latency regression"
    assert start.canonical_state_changed is False
    assert start.raw_text_retained is False

    continued = router.route(
        "Compare the new evidence with the baseline",
        session("project:tui", "thread:tui"),
    )
    assert continued.intent == ConversationIntent.CONTINUE_THREAD
    assert continued.method == "thread/input"
    assert continued.arguments["instruction"] == "Compare the new evidence with the baseline"


def test_missing_project_and_unknown_command_fail_closed_as_typed_hold() -> None:
    router = ConversationRouter()
    no_project = router.route("Start an investigation", session())
    assert no_project.intent == ConversationIntent.UNKNOWN_HOLD
    assert no_project.state == "HOLD"
    assert no_project.missing_fields == ("active_project_id",)

    unknown = router.route("/deploy production", session("project:tui", "thread:tui"))
    assert unknown.intent == ConversationIntent.UNKNOWN_HOLD
    assert unknown.method is None
    assert unknown.authority_required is False


def test_retry_and_usage_do_not_append_free_text() -> None:
    router = ConversationRouter()
    retry = router.route("/retry", session("project:tui", "thread:tui"))
    assert retry.intent == ConversationIntent.RETRY_THREAD
    assert retry.method == "thread/read"
    usage = router.route("/usage", session("project:tui", "thread:tui"))
    assert usage.intent == ConversationIntent.REFRESH_USAGE
    assert usage.arguments["refresh_account_quota"] is True
