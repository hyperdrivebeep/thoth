"""Comparing two stored results lists each criterion's change, and says when it cannot."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.apps.runtime_types import AppRuntime


def final_result_digest(runtime: AppRuntime, thread_id: str, request_digest: str) -> str:
    """The last stored result for the request: earlier ones are checkpoints without record refs."""
    found = ""
    for revision in runtime.ledger.read_revisions("p", "DECISION_OBJECT", f"result:{thread_id}"):
        snapshot = runtime.ledger.read_snapshot(revision.snapshot_id)
        ref = None if snapshot is None else snapshot.content.get("request_ref")
        if (
            isinstance(ref, dict)
            and cast(dict[str, object], ref).get("revision_digest") == request_digest
        ):
            found = revision.revision_digest
    assert found
    return found


@pytest.mark.asyncio
async def test_compare_lists_added_criteria_and_keeps_the_ones_that_stayed(tmp_path: Path) -> None:
    model = ControlledResearchModel(na=False)
    runtime = await setup(tmp_path, model, source=True)
    try:
        first = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "first",
                    {
                        "project_id": "p",
                        "problem": "LAB-42 지연 조건을 요약해줘",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        model.na = True  # the second run's plan adds one research check
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "second",
                    {
                        "project_id": "p",
                        "thread_id": first["thread_id"],
                        "instruction": "두 번째 조건",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        turns = value(
            await runtime.bus.query(
                request(
                    "thread/activity/list",
                    "conversation",
                    {"project_id": "p", "thread_id": first["thread_id"]},
                )
            )
        )["conversation"]["turns"]
        before_request, after_request = (
            turns[0]["request_revision_digest"],
            turns[1]["request_revision_digest"],
        )
        delta = value(
            await runtime.bus.query(
                request(
                    "thread/result/compare/read",
                    "compare",
                    {
                        "project_id": "p",
                        "thread_id": first["thread_id"],
                        "before": {
                            "request_revision_digest": before_request,
                            "result_revision_digest": final_result_digest(
                                runtime, first["thread_id"], before_request
                            ),
                        },
                        "after": {
                            "request_revision_digest": after_request,
                            "result_revision_digest": final_result_digest(
                                runtime, first["thread_id"], after_request
                            ),
                        },
                    },
                )
            )
        )
        assert delta["criteria_state"] == "AVAILABLE"
        criteria = cast(list[dict[str, object]], delta["criteria"])
        added = [item for item in criteria if item["match"] == "ADDED"]
        optional = [item for item in added if item["target"] == "optional"]
        assert len(optional) == 1
        assert optional[0]["before"] is None and optional[0]["after"] is not None
        # bound:0 has the same id in both runs but its question text carries the changed request,
        # so it is a removed criterion and an added one, not a changed one.
        assert sorted(
            str(item["match"]) for item in criteria if item["requirement_id"] == "bound:0"
        ) == [
            "ADDED",
            "REMOVED",
        ]
    finally:
        runtime.close()
