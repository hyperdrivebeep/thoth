"""A project cutoff typed with sub-millisecond digits must not make a fresh answer look out of date.

The project keeps the cutoff exactly as typed, a stored research result keeps it to the
millisecond. Comparing the two as plain datetimes marked every such answer "review required"
(POLICY_OR_CUTOFF_CHANGED) the moment it finished. A real change of a millisecond or more must
still be seen."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from tests.integration.scoped_runtime import fixture_scope_policy
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel

from thoth.apps.runtime import create_runtime


async def finished_thread(tmp_path: Path, cutoff: str) -> tuple[Any, str]:
    runtime = create_runtime(
        tmp_path,
        model_resolver=ControlledResearchModel(),
        resource_scope_policy=fixture_scope_policy(),
    )
    value(
        await runtime.bus.dispatch(
            request(
                "project/create",
                "project",
                {
                    "project_id": "p",
                    "name": "cutoff precision",
                    "cutoff_at": cutoff,
                    "overlay": "general-rnd",
                },
            )
        )
    )
    (tmp_path / "inbox").mkdir(exist_ok=True)
    (tmp_path / "inbox" / "records.html").write_text(
        '<html><head><meta property="article:published_time" content="2026-09-01T00:00:00Z" />'
        "</head><body><h1>Latency report</h1><p>기록 ID LAB-42: 지연 12 ms</p></body></html>",
        encoding="utf-8",
    )
    value(
        await runtime.bus.dispatch(
            request(
                "project/source/connect",
                "source",
                {
                    "project_id": "p",
                    "relative_path": "records.html",
                    "media_type": "text/html",
                    "authority": "INFORMAL",
                    "cutoff_state": "ELIGIBLE",
                    "security_class": "INTERNAL",
                },
            )
        )
    )
    accepted = value(
        await runtime.bus.dispatch(
            request(
                "thread/start",
                "start",
                {"project_id": "p", "problem": "LAB-42의 지연은?", "contract_version": 2},
            )
        )
    )
    await runtime.bus.drain()
    return runtime, str(accepted["thread_id"])


async def currentness(runtime: Any, thread: str) -> dict[str, Any]:
    status = value(
        await runtime.bus.dispatch(
            request(
                "thread/read",
                f"read-{uuid4()}",
                {"project_id": "p", "thread_id": thread, "view": "FULL"},
            )
        )
    )
    assert status["current_result"] is not None or status["previous_result"] is not None
    return {**status["basis_currentness"], "fresh": status["freshness"] == "CURRENT"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cutoff",
    [
        "2026-09-13T00:00:00.123456Z",  # typed to the microsecond
        "2026-09-13T09:00:00.123456+09:00",  # the same instant in another time zone
        "2026-09-13T00:00:00.123Z",  # already a millisecond
    ],
)
async def test_a_finished_answer_stays_current_whatever_precision_the_cutoff_was_typed_in(
    tmp_path: Path, cutoff: str
) -> None:
    runtime, thread = await finished_thread(tmp_path, cutoff)
    try:
        state = await currentness(runtime, thread)
        assert state["state"] == "CURRENT" and state["reasons"] == [], state
        assert state["fresh"] is True
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_cutoff_changed_by_a_millisecond_is_still_a_change(tmp_path: Path) -> None:
    runtime, thread = await finished_thread(tmp_path, "2026-09-13T00:00:00.123456Z")
    try:
        assert (await currentness(runtime, thread))["state"] == "CURRENT"
        proposed = "2026-09-13T00:00:00.124456Z"  # one millisecond later
        impact = value(
            await runtime.bus.dispatch(
                request(
                    "project/cutoff/impact",
                    "impact",
                    {"project_id": "p", "proposed_cutoff_at": proposed},
                )
            )
        )["impact"]
        project = value(
            await runtime.bus.dispatch(request("project/read", "pr", {"project_id": "p"}))
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "project/cutoff/update",
                    "update",
                    {
                        "project_id": "p",
                        "expected_revision": project["revision"],
                        "cutoff_at": proposed,
                        "expected_impact_digest": impact["impact_digest"],
                    },
                )
            )
        )
        state = await currentness(runtime, thread)
        assert (
            state["state"] == "REVIEW_REQUIRED" and "POLICY_OR_CUTOFF_CHANGED" in state["reasons"]
        ), state
    finally:
        runtime.close()
