"""Effort bands travel from the planning model through the normal research entry to the record."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.adapters.runtime import SystemClock
from thoth.adapters.storage import SqliteConversationSessionStore
from thoth.application.services.conversation_router import ConversationRouter
from thoth.application.services.tui_session_service import TuiSessionService
from thoth.apps.conversation_dispatch import BusConversationDispatcher
from thoth.apps.runtime_types import AppRuntime
from thoth.domain.action import ActionPlanDraft, EffortEstimateDraft
from thoth.domain.canonical import model_digest
from thoth.domain.enums import ModelRole
from thoth.domain.model import ModelRequest, ModelResult


class EffortModel(ControlledResearchModel):
    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        result = await super().structured(request)
        if request.role != ModelRole.ACTION_PLANNER:
            return result
        draft = cast(ActionPlanDraft, result.output)
        first, second = draft.alternatives
        first = first.model_copy(
            update={
                "effort_estimates": (
                    EffortEstimateDraft(
                        dimension="TIME", band="MEDIUM", basis_text="담당자 회신이 필요"
                    ),
                    EffortEstimateDraft(dimension="COST_EFFORT", band="HIGH", basis_text=""),
                )
            }
        )
        output = draft.model_copy(update={"alternatives": (first, second)})
        return ModelResult(
            output=cast(T, output),
            model_id=result.model_id,
            scripted=True,
            prompt_version=result.prompt_version,
            input_digest=result.input_digest,
            output_digest=model_digest("OUTPUT", output, schema_version="1.0.0"),
        )


async def _run(tmp_path: Path):
    model = EffortModel()
    runtime = await setup(tmp_path, model)
    tui = TuiSessionService(
        session_id="test:tui",
        store=SqliteConversationSessionStore(runtime.ledger.engine),
        router=ConversationRouter(),
        dispatcher=BusConversationDispatcher(runtime.bus),
        clock=SystemClock(),
    )
    turn = await tui.execute("LAB-42의 지연과 조건을 알려줘")
    await runtime.bus.drain()
    operation = runtime.bus.read_operation(str(turn.response["operation_id"]))
    assert operation is not None and operation.state.value == "SUCCEEDED", operation
    listed = value(
        await runtime.bus.dispatch(request("action/list", "actions", {"project_id": "p"}))
    )
    return runtime, cast(list[dict[str, object]], listed["actions"])


async def _read(runtime: AppRuntime, action_id: str) -> dict[str, object]:
    read = value(
        await runtime.bus.dispatch(
            request("action/read", f"read-{action_id}", {"project_id": "p", "action_id": action_id})
        )
    )
    return cast(dict[str, object], read["action"])


def _estimates(action: dict[str, object]) -> list[dict[str, object]]:
    details = cast(dict[str, object], action["generation_details"])
    return cast(list[dict[str, object]], details["effort_estimates"])


@pytest.mark.asyncio
async def test_model_estimates_are_recorded_with_unsupported_bands_as_unknown(
    tmp_path: Path,
) -> None:
    runtime, summaries = await _run(tmp_path)
    try:
        by_id = {str(item["action_id"]): item for item in summaries}
        first = await _read(runtime, next(key for key in by_id if key.endswith(":0")))
        second = await _read(runtime, next(key for key in by_id if key.endswith(":1")))
        assert [(e["dimension"], e["band"], e["estimator_type"]) for e in _estimates(first)] == [
            ("TIME", "MEDIUM", "AI"),
            ("COST_EFFORT", "UNKNOWN", "AI"),
        ]
        assert all(e["created_at"] for e in _estimates(first))
        assert _estimates(second) == []
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_human_estimate_makes_a_new_revision_and_keeps_the_ai_estimate(
    tmp_path: Path,
) -> None:
    runtime, summaries = await _run(tmp_path)
    try:
        action_id = next(
            str(item["action_id"]) for item in summaries if str(item["action_id"]).endswith(":0")
        )
        before = await _read(runtime, action_id)
        revised = value(
            await runtime.bus.dispatch(
                request(
                    "action/revise",
                    "human-effort",
                    {
                        "project_id": "p",
                        "action_id": action_id,
                        "expected_revision_digest": before["revision_digest"],
                        "patch": {},
                        "evidence_refs": [],
                        "reason": "담당자에게 물어본 결과",
                        "estimator_ref": "human:owner",
                        "human_effort_estimates": [
                            {"dimension": "TIME", "band": "HIGH", "basis_text": "현장 시험이 필요"}
                        ],
                    },
                )
            )
        )
        after = cast(dict[str, object], revised["action"])
        assert after["revision_digest"] != before["revision_digest"]
        assert after["supersedes_revision_digest"] == before["revision_digest"]
        kinds = [(e["dimension"], e["band"], e["estimator_type"]) for e in _estimates(after)]
        assert kinds == [
            ("TIME", "MEDIUM", "AI"),
            ("COST_EFFORT", "UNKNOWN", "AI"),
            ("TIME", "HIGH", "HUMAN"),
        ]
        human = _estimates(after)[-1]
        assert human["estimator_ref"] == "human:owner" and human["created_at"]
        # the earlier revision is unchanged and still reads with only the AI estimates
        assert len(_estimates(before)) == 2
        page = value(
            await runtime.bus.query(
                request(
                    "revision/timeline/read",
                    "effort-history",
                    {"project_id": "p", "scope": {"project_id": "p"}, "limit": 50},
                )
            )
        )
        entry = next(
            item
            for item in cast(list[dict[str, object]], page["items"])
            if cast(dict[str, object], item["record_ref"])["revision_digest"]
            == after["revision_digest"]
        )
        summary = cast(dict[str, object], entry["change_summary"])
        (line,) = cast(list[dict[str, object]], summary["lines"])
        assert (line["label"], line["before"], line["after"]) == (
            "시간 추정",
            "보통(AI)",
            "김(사람)",
        )
        assert "generation_details" not in str(summary["lines"]) and summary["other"] == 0
        # A human band without a basis sentence is rejected, not stored.
        bad = await runtime.bus.dispatch(
            request(
                "action/revise",
                "human-effort-bad",
                {
                    "project_id": "p",
                    "action_id": action_id,
                    "expected_revision_digest": after["revision_digest"],
                    "patch": {},
                    "evidence_refs": [],
                    "reason": "no basis",
                    "estimator_ref": "human:owner",
                    "human_effort_estimates": [
                        {"dimension": "TIME", "band": "LOW", "basis_text": ""}
                    ],
                },
            )
        )
        assert bad.error is not None
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_human_estimate_waits_for_a_running_investigation_which_finishes_unbranched(
    tmp_path: Path,
) -> None:
    model = EffortModel()
    runtime = await setup(tmp_path, model)
    tui = TuiSessionService(
        session_id="test:tui",
        store=SqliteConversationSessionStore(runtime.ledger.engine),
        router=ConversationRouter(),
        dispatcher=BusConversationDispatcher(runtime.bus),
        clock=SystemClock(),
    )

    def estimate(action_id: str, key: str, digest: object):  # type: ignore[no-untyped-def]
        return request(
            "action/revise",
            key,
            {
                "project_id": "p",
                "action_id": action_id,
                "expected_revision_digest": digest,
                "patch": {},
                "evidence_refs": [],
                "reason": "담당자에게 물어본 결과",
                "estimator_ref": "human:owner",
                "human_effort_estimates": [
                    {"dimension": "TIME", "band": "HIGH", "basis_text": "현장 시험이 필요"}
                ],
            },
        )

    try:
        await tui.execute("LAB-42의 지연과 조건을 알려줘")
        await runtime.bus.drain()
        listed = value(
            await runtime.bus.dispatch(request("action/list", "actions", {"project_id": "p"}))
        )
        action_id = next(
            str(item["action_id"])
            for item in cast(list[dict[str, object]], listed["actions"])
            if str(item["action_id"]).endswith(":0")
        )
        model.started.clear()
        model.release.clear()
        running = asyncio.create_task(tui.execute("LAB-42의 조건을 다시 알려줘"))
        await asyncio.wait_for(model.started.wait(), 60)
        before = await _read(runtime, action_id)
        heads = dict(runtime.ledger.read_heads("p"))
        refused = await runtime.bus.dispatch(
            estimate(action_id, "human-while-running", before["revision_digest"])
        )
        assert refused.error is not None
        assert "ACTION_EDIT_RESEARCH_RUNNING" in refused.error.message
        assert dict(runtime.ledger.read_heads("p")) == heads
        model.release.set()
        await asyncio.wait_for(running, 120)
        await runtime.bus.drain()
        again = value(
            await runtime.bus.dispatch(request("action/list", "actions-after", {"project_id": "p"}))
        )
        latest = cast(list[dict[str, object]], again["actions"])[-1]
        action_id = str(latest["action_id"])
        current = await _read(runtime, action_id)
        done = await runtime.bus.dispatch(
            estimate(action_id, "human-after-running", current["revision_digest"])
        )
        assert done.error is None
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_human_estimate_is_refused_when_an_investigation_starts_before_the_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from thoth.application.commands import action_effort

    runtime, summaries = await _run(tmp_path)
    try:
        action_id = next(
            str(item["action_id"]) for item in summaries if str(item["action_id"]).endswith(":0")
        )
        before = await _read(runtime, action_id)
        heads = dict(runtime.ledger.read_heads("p"))
        calls: list[int] = []

        def starts_between(operations: object, project_id: str) -> bool:
            calls.append(1)
            return len(calls) > 1  # quiet at the first check, running by the time of the save

        monkeypatch.setattr(action_effort, "research_is_running", starts_between)
        refused = await runtime.bus.dispatch(
            request(
                "action/revise",
                "human-race",
                {
                    "project_id": "p",
                    "action_id": action_id,
                    "expected_revision_digest": before["revision_digest"],
                    "patch": {},
                    "evidence_refs": [],
                    "reason": "담당자에게 물어본 결과",
                    "estimator_ref": "human:owner",
                    "human_effort_estimates": [
                        {"dimension": "TIME", "band": "HIGH", "basis_text": "현장 시험이 필요"}
                    ],
                },
            )
        )
        assert len(calls) == 2
        assert refused.error is not None and "ACTION_EDIT_RESEARCH_RUNNING" in refused.error.message
        assert dict(runtime.ledger.read_heads("p")) == heads
        assert (await _read(runtime, action_id))["revision_digest"] == before["revision_digest"]
    finally:
        runtime.close()
