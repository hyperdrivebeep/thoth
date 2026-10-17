"""Normal read projection order and shared payload observations before extraction."""

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
from pydantic import JsonValue
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.application.services import (
    research_followup_projection,
    research_progress_view,
    user_activity_projection,
)
from thoth.application.services.user_activity_redaction import SourceActivityContext


@pytest.mark.parametrize("view", ["SUMMARY", "FULL", "PROGRESS", "FAILURE"])
async def test_view_projection_order_and_shared_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    view: str,
) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=True)
    try:
        started = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "view-start",
                    {
                        "project_id": "p",
                        "problem": "LAB-42 지연 조건",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        events: list[str] = []
        payloads: list[dict[str, JsonValue]] = []
        activity_results: list[list[JsonValue]] = []
        fault = RuntimeError("controlled followup projection failure")
        old_activity = research_progress_view.project_activity_events
        old_user_activity = user_activity_projection.project_user_activity_events

        def followup[**P, R](call: Callable[P, R]) -> Callable[P, R]:
            def invoke(*args: P.args, **kwargs: P.kwargs) -> R:
                events.append("followup")
                if view == "FAILURE":
                    raise fault
                return call(*args, **kwargs)

            return invoke

        def activity(payload: dict[str, JsonValue]) -> list[JsonValue]:
            events.append("activity")
            payloads.append(payload)
            fields = ("user_progress_summary", "coverage_matrix", "next_user_action")
            if view == "PROGRESS":
                assert all(field not in payload for field in fields)
            else:
                assert all(field in payload for field in fields)
                positions = [list(payload).index(field) for field in fields]
                assert positions == sorted(positions)
            result = old_activity(payload)
            activity_results.append(result)
            return result

        def user_activity(
            payload: Mapping[str, object],
            *,
            source_context: Mapping[str, SourceActivityContext] | None = None,
        ) -> list[dict[str, JsonValue]]:
            events.append("user_activity")
            assert payload is payloads[0]
            assert payload["activity_events"] is activity_results[0]
            return old_user_activity(payload, source_context=source_context)

        params: dict[str, object] = {
            "project_id": "p",
            "thread_id": started["thread_id"],
            "view": "FULL" if view == "FAILURE" else view,
        }
        original_params = dict(params)
        with monkeypatch.context() as query_patch:
            query_patch.setattr(
                research_followup_projection,
                "project_thread_followup",
                followup(research_followup_projection.project_thread_followup),
            )
            query_patch.setattr(research_progress_view, "project_activity_events", activity)
            query_patch.setattr(
                user_activity_projection, "project_user_activity_events", user_activity
            )
            if view == "FAILURE":
                with pytest.raises(RuntimeError) as caught:
                    await runtime.bus.query(request("thread/read", "view-read", params))
                assert caught.value is fault
                assert events == ["followup"] and payloads == []
            else:
                result = value(await runtime.bus.query(request("thread/read", "view-read", params)))
                assert result["view"] == view
                assert events == ([] if view == "PROGRESS" else ["followup"]) + [
                    "activity",
                    "user_activity",
                ]
                assert len(payloads) == 1
                assert "user_activity_events" in payloads[0]
                assert list(payloads[0]).index("activity_events") < list(payloads[0]).index(
                    "user_activity_events"
                )
                if view in {"FULL", "PROGRESS"}:
                    assert result["user_activity_events"] == payloads[0]["user_activity_events"]
        assert params == original_params
    finally:
        runtime.close()
