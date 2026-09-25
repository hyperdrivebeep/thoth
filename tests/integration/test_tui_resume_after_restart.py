from __future__ import annotations

from pathlib import Path

import pytest
from tests.integration.test_a02_autonomous_acquisition import prepare_thread

from thoth.adapters.runtime import SystemClock
from thoth.adapters.storage.conversation import SqliteConversationSessionStore
from thoth.application.services.conversation_router import ConversationRouter
from thoth.application.services.tui_session_service import TuiSessionService
from thoth.apps.conversation_dispatch import BusConversationDispatcher
from thoth.apps.runtime import create_runtime


@pytest.mark.asyncio
async def test_tui_context_resumes_after_runtime_restart_without_storing_free_text(
    tmp_path: Path,
) -> None:
    runtime, _connector, project_id = await prepare_thread(tmp_path, allow_connector=True)
    workspace = tmp_path / "allowed"
    thread_id = f"thread:{project_id}"
    first = TuiSessionService(
        session_id="tui:resume",
        store=SqliteConversationSessionStore(runtime.ledger.engine),
        router=ConversationRouter(),
        dispatcher=BusConversationDispatcher(runtime.bus),
        clock=SystemClock(),
    )
    await first.execute(f"/project {project_id}")
    await first.execute(f"/thread use {thread_id}")
    runtime.close()

    reopened = create_runtime(workspace)
    try:
        second = TuiSessionService(
            session_id="tui:resume",
            store=SqliteConversationSessionStore(reopened.ledger.engine),
            router=ConversationRouter(),
            dispatcher=BusConversationDispatcher(reopened.bus),
            clock=SystemClock(),
        )
        state = second.current()
        assert state.active_project_id == project_id
        assert state.active_thread_id == thread_id
        assert "Find the missing" not in state.model_dump_json()
        status = await second.execute("/status")
        assert status.status == "DISPATCHED"
    finally:
        reopened.close()
