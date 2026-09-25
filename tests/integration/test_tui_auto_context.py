from __future__ import annotations

from pathlib import Path

import pytest
from tests.integration.test_a02_autonomous_acquisition import prepare_thread, request, value

from thoth.adapters.runtime import SystemClock
from thoth.adapters.storage.conversation import SqliteConversationSessionStore
from thoth.application.services.conversation_router import ConversationRouter
from thoth.application.services.tui_session_service import TuiSessionService
from thoth.apps.conversation_dispatch import BusConversationDispatcher
from thoth.apps.runtime import AppRuntime
from thoth.cli import workspace_session_id
from thoth.domain.conversation import ConversationIntent


def service(runtime: AppRuntime, session_id: str) -> TuiSessionService:
    ledger = runtime.ledger
    bus = runtime.bus
    return TuiSessionService(
        session_id=session_id,
        store=SqliteConversationSessionStore(ledger.engine),
        router=ConversationRouter(),
        dispatcher=BusConversationDispatcher(bus),
        clock=SystemClock(),
    )


@pytest.mark.asyncio
async def test_single_project_and_latest_thread_resume_without_setup_commands(
    tmp_path: Path,
) -> None:
    runtime, _connector, project_id = await prepare_thread(tmp_path, allow_connector=True)
    try:
        tui = service(runtime, "tui:auto-context")

        result = await tui.execute("Find the missing dataset version and compare hypotheses")

        assert result.status == "DISPATCHED"
        assert result.candidate.intent == ConversationIntent.CONTINUE_THREAD
        assert result.session.active_project_id == project_id
        assert result.session.active_thread_id == f"thread:{project_id}"
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_new_clears_only_thread_context_and_next_text_starts_thread(
    tmp_path: Path,
) -> None:
    runtime, _connector, project_id = await prepare_thread(tmp_path, allow_connector=True)
    try:
        tui = service(runtime, "tui:new-thread")
        await tui.execute("Continue the existing investigation")

        reset = await tui.execute("/new")

        assert reset.status == "DISPATCHED"
        assert reset.session.active_project_id == project_id
        assert reset.session.active_thread_id is None
        assert reset.response["awaiting_problem"] is True

        started = await tui.execute("Investigate a separate calibration problem")
        assert started.status == "DISPATCHED"
        assert started.candidate.intent == ConversationIntent.START_THREAD
        assert started.session.active_thread_id is not None
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_multiple_unbound_projects_hold_instead_of_guessing(tmp_path: Path) -> None:
    runtime, _connector, _project_id = await prepare_thread(tmp_path, allow_connector=True)
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "second-project",
                    {
                        "project_id": "project:second",
                        "name": "Second project",
                        "cutoff_at": "2026-09-03T00:00:00Z",
                    },
                )
            )
        )
        tui = service(runtime, "tui:ambiguous")

        result = await tui.execute("Do not guess which project owns this problem")

        assert result.status == "HOLD"
        assert result.candidate.missing_fields == ("active_project_id",)
    finally:
        runtime.close()


def test_workspace_session_id_is_stable_and_workspace_scoped(tmp_path: Path) -> None:
    first = workspace_session_id(tmp_path / "alpha")
    same = workspace_session_id(tmp_path / "alpha" / ".." / "alpha")
    other = workspace_session_id(tmp_path / "beta")

    assert first == same
    assert first != other
    assert str(tmp_path) not in first
    assert first.startswith("tui:workspace:")
