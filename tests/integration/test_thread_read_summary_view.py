"""thread/read sends what the first screen needs; the rest is read when it is opened."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.application.services.research_read_summary import (
    OMITTED_MANIFEST_FIELDS,
    OMITTED_RESULT_KEYS,
)


async def read(runtime: Any, key: str, thread: str, **extra: Any) -> dict[str, Any]:
    return value(
        await runtime.bus.query(
            request("thread/read", key, {"project_id": "p", "thread_id": thread, **extra})
        )
    )


@pytest.mark.asyncio
async def test_the_default_read_leaves_out_what_the_first_screen_does_not_use(
    tmp_path: Path,
) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=True)
    try:
        started = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "s",
                    {
                        "project_id": "p",
                        "problem": "LAB-42 지연 조건을 요약해줘",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        thread = started["thread_id"]
        summary = await read(runtime, "r1", thread)
        full = await read(runtime, "r2", thread, view="FULL")
        assert summary["view"] == "SUMMARY" and full["view"] == "FULL"
        shown, whole = summary["current_result"], full["current_result"]
        for name in OMITTED_MANIFEST_FIELDS:
            assert name not in shown and name in whole
        omitted_present = [key for key in OMITTED_RESULT_KEYS if key in whole["result"]]
        assert omitted_present, "the controlled run should carry some of the omitted internals"
        for key in omitted_present:
            assert key not in shown["result"]
        # what the screen reads is still there, and identical to the full read
        for key in (
            "answer",
            "answer_status",
            "portfolio",
            "action_plan",
            "selected_evidence_refs",
        ):
            assert shown["result"].get(key) == whole["result"].get(key)
        for key in ("coverage_matrix", "user_progress_summary", "next_user_action", "result_usage"):
            assert summary[key] == full[key]
        assert (
            shown["operation_id"] == whole["operation_id"]
            and shown["source_refs"] == whole["source_refs"]
        )
        assert len(json.dumps(summary)) < len(json.dumps(full))
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_an_unknown_view_is_rejected_not_silently_treated_as_one(tmp_path: Path) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=True)
    try:
        started = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "s",
                    {
                        "project_id": "p",
                        "problem": "LAB-42 지연 조건을 요약해줘",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        response = await runtime.bus.query(
            request(
                "thread/read",
                "bad",
                {"project_id": "p", "thread_id": started["thread_id"], "view": "EVERYTHING"},
            )
        )
        assert response.error is not None
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_the_progress_view_skips_the_result_and_the_criteria_projection(
    tmp_path: Path,
) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=True)
    try:
        started = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "s",
                    {
                        "project_id": "p",
                        "problem": "LAB-42 지연 조건을 요약해줘",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        thread = started["thread_id"]
        progress = await read(runtime, "r1", thread, view="PROGRESS")
        summary = await read(runtime, "r2", thread)
        assert progress["view"] == "PROGRESS"
        # the running-state parts a progress panel needs are the same as in the summary
        for key in ("operation_state", "usage", "result_usage", "model_dispatches", "attempt"):
            assert progress[key] == summary[key]
        full = await read(runtime, "r3", thread, view="FULL")
        assert progress["user_activity_events"] == full["user_activity_events"]
        # a finished operation's activity log is only in the full read and the progress read
        assert "user_activity_events" not in summary and "activity_events" not in summary
        # the stored result and the criteria projection are not sent or computed
        for key in (
            "current_result",
            "previous_result",
            "coverage_matrix",
            "user_progress_summary",
        ):
            assert key not in progress
        assert len(json.dumps(progress)) < len(json.dumps(full))
    finally:
        runtime.close()
