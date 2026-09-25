from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from pydantic import JsonValue

from thoth.application.services.conversation_router import ConversationRouter
from thoth.domain.conversation import (
    ConversationIntent,
    ConversationIntentState,
    TuiSessionState,
    TuiTurnResult,
    TuiTurnStatus,
)
from thoth.domain.oauth_retry import allows_once_transient_429
from thoth.ports.conversation import (
    ConversationDispatcherPort,
    ConversationSessionStorePort,
)
from thoth.ports.runtime import ClockPort


class TuiSessionService:
    def __init__(
        self,
        *,
        session_id: str,
        store: ConversationSessionStorePort,
        router: ConversationRouter,
        dispatcher: ConversationDispatcherPort,
        clock: ClockPort,
    ) -> None:
        self._session_id = session_id
        self._store = store
        self._router = router
        self._dispatcher = dispatcher
        self._clock = clock

    def current(self) -> TuiSessionState:
        return self._store.read(self._session_id) or TuiSessionState(
            session_id=self._session_id,
            revision=0,
            updated_at=self._clock.now(),
        )

    async def execute(self, raw: str) -> TuiTurnResult:
        session = await self._bootstrap(self.current())
        candidate = self._router.route(raw, session)
        if (
            candidate.state == ConversationIntentState.READY
            and candidate.intent == ConversationIntent.NEW_THREAD_CONTEXT
        ):
            updated = session.model_copy(
                update={
                    "active_thread_id": None,
                    "revision": session.revision + 1,
                    "updated_at": self._clock.now(),
                }
            )
            self._store.put(updated)
            return TuiTurnResult(
                status=TuiTurnStatus.DISPATCHED,
                candidate=candidate,
                session=updated,
                response={"awaiting_problem": True},
            )
        if candidate.state == ConversationIntentState.HOLD or candidate.method is None:
            return TuiTurnResult(
                status=TuiTurnStatus.HOLD,
                candidate=candidate,
                session=session,
            )
        if candidate.method == "model/settings/update":
            selection = cast(dict[str, JsonValue], candidate.arguments["selection"])
            scope = {
                k: v for k, v in candidate.arguments.items() if k in {"project_id", "thread_id"}
            }
            current = await self._dispatcher.dispatch(
                method="model/settings/read",
                arguments={**scope, "selection": selection},
                idempotency_key=f"tui:{session.session_id}:{session.revision}:model-settings-read",
            )
            if not current.success:
                return TuiTurnResult(
                    status=TuiTurnStatus.HOLD,
                    candidate=candidate,
                    session=session,
                    error_code=current.error_code,
                    error_message=current.error_message,
                )
            if candidate.intent == ConversationIntent.SET_REASONING:
                selection = {**cast(dict[str, JsonValue], current.value["selection"]), **selection}
            candidate = candidate.model_copy(
                update={
                    "arguments": {
                        **candidate.arguments,
                        "selection": selection,
                        "expected_digest": current.value["settings_digest"],
                    }
                }
            )
        if candidate.intent == ConversationIntent.RETRY_THREAD:
            current = await self._dispatcher.dispatch(
                method="thread/read",
                arguments=candidate.arguments,
                idempotency_key=f"tui:{session.session_id}:{session.revision}:retry-read",
            )
            if not current.success:
                return TuiTurnResult(
                    status=TuiTurnStatus.HOLD,
                    candidate=candidate,
                    session=session,
                    error_code=current.error_code,
                    error_message=current.error_message,
                )
            request = current.value.get("request")
            request = request if isinstance(request, dict) else {}
            question = request.get("effective_question")
            epoch = request.get("request_epoch")
            if not isinstance(question, str) or not question.strip() or type(epoch) is not int:
                return TuiTurnResult(
                    status=TuiTurnStatus.HOLD,
                    candidate=candidate,
                    session=session,
                    error_code=None,
                    error_message="RETRY_QUESTION_UNAVAILABLE",
                )
            nested = request.get("model_settings")
            nested = nested if isinstance(nested, dict) else {}
            selected = {
                key: value
                for source in (nested, request)
                for key, value in source.items()
                if key in {"provider", "model", "reasoning_effort"} and value is not None
            }
            current_settings = await self._dispatcher.dispatch(
                method="model/settings/read",
                arguments={
                    "project_id": candidate.arguments["project_id"],
                    "thread_id": candidate.arguments["thread_id"],
                },
                idempotency_key=f"tui:{session.session_id}:{session.revision}:retry-settings",
            )
            if not current_settings.success:
                return TuiTurnResult(
                    status=TuiTurnStatus.HOLD,
                    candidate=candidate,
                    session=session,
                    error_code=current_settings.error_code,
                    error_message=current_settings.error_message,
                )
            effective = current_settings.value.get("effective_settings")
            effective = effective if isinstance(effective, dict) else {}
            provider = effective.get("provider")
            capability_source = effective.get("capability_source")
            current_selection = {
                key: effective.get(key)
                for key in ("provider", "model", "reasoning_effort")
                if effective.get(key) is not None
            }
            if selected and current_selection != selected:
                return TuiTurnResult(
                    status=TuiTurnStatus.HOLD,
                    candidate=candidate,
                    session=session,
                    error_message="RETRY_MODEL_SELECTION_CHANGED",
                )
            arguments = {
                "project_id": candidate.arguments["project_id"],
                "thread_id": candidate.arguments["thread_id"],
                "instruction": question,
                "edit_kind": "REPLACE",
                "expected_request_epoch": epoch,
                "contract_version": 2,
                "prior_operation_id": request.get("operation_id"),
                **selected,
            }
            if allows_once_transient_429(
                provider=(
                    provider if isinstance(provider, str) else None
                ),
                capability_source=(
                    capability_source if isinstance(capability_source, str) else None
                ),
            ):
                arguments["retry_policy"] = "ONCE_TRANSIENT_429"
            candidate = candidate.model_copy(
                update={"method": "thread/input", "arguments": arguments}
            )
        assert candidate.method is not None
        outcome = await self._dispatcher.dispatch(
            method=candidate.method,
            arguments=candidate.arguments,
            idempotency_key=(
                f"tui:{session.session_id}:{session.revision}:{candidate.raw_input_digest}"
            ),
        )
        if not outcome.success:
            return TuiTurnResult(
                status=TuiTurnStatus.HOLD,
                candidate=candidate,
                session=session,
                error_code=outcome.error_code,
                error_message=outcome.error_message,
            )
        updated = self._after_success(session, candidate.intent, candidate.arguments, outcome.value)
        self._store.put(updated)
        return TuiTurnResult(
            status=TuiTurnStatus.DISPATCHED,
            candidate=candidate,
            session=updated,
            response=outcome.value,
        )

    async def _bootstrap(self, session: TuiSessionState) -> TuiSessionState:
        if session.revision != 0 or session.active_project_id is not None:
            return session
        projects = await self._dispatcher.dispatch(
            method="project/list",
            arguments={"project_id": "system:projects"},
            idempotency_key=f"tui:{session.session_id}:bootstrap:projects",
        )
        project_values = projects.value.get("projects") if projects.success else None
        if not isinstance(project_values, list) or len(project_values) != 1:
            return session
        project = project_values[0]
        if not isinstance(project, dict) or not isinstance(project.get("project_id"), str):
            return session
        project_id = str(project["project_id"])
        threads = await self._dispatcher.dispatch(
            method="thread/list",
            arguments={"project_id": project_id},
            idempotency_key=f"tui:{session.session_id}:bootstrap:threads:{project_id}",
        )
        thread_values = threads.value.get("threads") if threads.success else None
        thread_id: str | None = None
        if isinstance(thread_values, list):
            candidates = [
                item
                for item in thread_values
                if isinstance(item, dict) and isinstance(item.get("thread_id"), str)
            ]
            if candidates:
                latest = max(candidates, key=lambda item: str(item.get("updated_at", "")))
                thread_id = str(latest["thread_id"])
        updated = session.model_copy(
            update={
                "active_project_id": project_id,
                "active_thread_id": thread_id,
                "revision": session.revision + 1,
                "updated_at": self._clock.now(),
            }
        )
        self._store.put(updated)
        return updated

    def _after_success(
        self,
        session: TuiSessionState,
        intent: ConversationIntent,
        arguments: Mapping[str, object],
        response: Mapping[str, object],
    ) -> TuiSessionState:
        project_id = session.active_project_id
        thread_id = session.active_thread_id
        if intent == ConversationIntent.SELECT_PROJECT:
            project_id = str(arguments["project_id"])
            thread_id = None
        elif intent in {ConversationIntent.SELECT_THREAD, ConversationIntent.START_THREAD}:
            project_id = str(arguments["project_id"])
            thread_id = str(response.get("thread_id") or arguments.get("thread_id") or "") or None
        return session.model_copy(
            update={
                "active_project_id": project_id,
                "active_thread_id": thread_id,
                "revision": session.revision + 1,
                "updated_at": self._clock.now(),
            }
        )
