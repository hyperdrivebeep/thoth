from __future__ import annotations

from pathlib import Path

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.adapters.runtime import SystemClock
from thoth.adapters.storage.conversation import SqliteConversationSessionStore
from thoth.application.services.conversation_router import ConversationRouter
from thoth.application.services.tui_session_service import TuiSessionService
from thoth.apps.conversation_dispatch import BusConversationDispatcher
from thoth.domain.conversation import ConversationIntent


@pytest.mark.asyncio
async def test_tui_selects_existing_context_and_runs_normal_thread_input(tmp_path: Path) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel())
    project_id = "p"
    thread_id = "thread:p"
    value(
        await runtime.bus.dispatch(
            request(
                "thread/start",
                "initial-thread",
                {"project_id": project_id, "thread_id": thread_id, "problem": "Inspect conditions"},
            )
        )
    )
    service = TuiSessionService(
        session_id="tui:full-cycle",
        store=SqliteConversationSessionStore(runtime.ledger.engine),
        router=ConversationRouter(),
        dispatcher=BusConversationDispatcher(runtime.bus),
        clock=SystemClock(),
    )
    try:
        selected_project = await service.execute(f"/project {project_id}")
        assert selected_project.status == "DISPATCHED"
        selected_thread = await service.execute(f"/thread use {thread_id}")
        assert selected_thread.status == "DISPATCHED"
        analyzed = await service.execute("Find the missing dataset version and compare hypotheses")
        assert analyzed.candidate.intent == ConversationIntent.CONTINUE_THREAD
        assert analyzed.status == "DISPATCHED"
        assert analyzed.response["status"] == "ACCEPTED_RUNNING"
        await runtime.bus.drain()
        operation = runtime.bus.read_operation(str(analyzed.response["operation_id"]))
        assert operation is not None and operation.state.value == "SUCCEEDED"
        response = operation.result
        assert response is not None
        assert response["coverage"]
        assert response["portfolio"]
        assert response["action_plan"]
        assert response["commit"]
        assert analyzed.session.active_project_id == project_id
        assert analyzed.session.active_thread_id == thread_id
    finally:
        runtime.close()
