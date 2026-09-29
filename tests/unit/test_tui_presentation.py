"""The extracted CLI renderer keeps a bounded user-facing turn summary."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from io import StringIO

from rich.console import Console

from thoth.apps.tui_presentation import render_tui_turn
from thoth.domain.conversation import (
    ConversationIntent,
    ConversationIntentCandidate,
    ConversationIntentState,
    TuiSessionState,
    TuiTurnResult,
    TuiTurnStatus,
)


def test_rendered_tui_turn_keeps_status_and_omits_unbounded_response_fields() -> None:
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=False, color_system=None)
    turn = TuiTurnResult(
        status=TuiTurnStatus.HOLD,
        candidate=ConversationIntentCandidate(
            intent=ConversationIntent.ASK_STATUS,
            state=ConversationIntentState.HOLD,
            display_message="근거를 확인 중입니다",
            raw_input_digest="a" * 64,
        ),
        session=TuiSessionState(
            session_id="tui:synthetic",
            active_project_id="project:synthetic",
            active_thread_id="thread:synthetic",
            revision=1,
            updated_at=datetime(2026, 9, 27, tzinfo=UTC),
        ),
        response={
            "status": "PARTIAL_HOLD",
            "portfolio": {"hypotheses": [{"hypothesis_id": "hypothesis:a"}]},
            "raw_source_text": "must-not-render",
        },
        error_code=-32030,
        error_message="SOURCE_TIME_UNCONFIRMED",
    )

    render_tui_turn(turn, console)

    payload = json.loads(buffer.getvalue())
    assert payload["status"] == "HOLD"
    assert payload["project_id"] == "project:synthetic"
    assert payload["hold"]["message"] == "SOURCE_TIME_UNCONFIRMED"
    assert payload["result"] == {"status": "PARTIAL_HOLD", "hypothesis_count": 1}
    assert "must-not-render" not in buffer.getvalue()
