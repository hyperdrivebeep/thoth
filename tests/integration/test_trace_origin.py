"""A trace row starts an investigation.

The origin is checked against the stored trace, reaches the model's context and the result, and the
thread list shows which row a thread belongs to.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup
from tests.integration.trace_demo_helpers import demo_set, with_notice

from thoth.application.services.trace_csv import export_csv, export_rows, write_csv
from thoth.apps.runtime import AppRuntime
from thoth.domain.enums import ModelRole
from thoth.protocol.bus import READ_QUERY_METHODS
from thoth.protocol.jsonrpc import JsonRpcResponse

DRY, FOG, RAIN = "SYN-C-DET-DRY", "SYN-C-DET-FOG", "SYN-C-DET-RAIN"


class Rpc:
    def __init__(self, runtime: AppRuntime) -> None:
        self.runtime, self.count = runtime, 0

    async def raw(self, method: str, **params: Any) -> JsonRpcResponse:
        self.count += 1
        message = request(method, f"o{self.count}", params)
        if method in READ_QUERY_METHODS:
            return await self.runtime.bus.query(message)
        return await self.runtime.bus.dispatch(message)

    async def __call__(self, method: str, **params: Any) -> dict[str, Any]:
        return value(await self.raw(method, **params))

    async def error(self, method: str, **params: Any) -> str:
        response = await self.raw(method, **params)
        assert response.error is not None, "expected an error"
        return response.error.message

    async def load(self, text: str, mode: str) -> None:
        preview = await self("trace/importPreview", project_id="p", mode=mode, csv_text=text)
        assert preview["applicable"], preview["conflicts"]
        await self(
            "trace/importApply",
            project_id="p",
            mode=mode,
            csv_text=text,
            preview_id=preview["preview_id"],
            input_sha256=preview["input_sha256"],
        )

    async def rain_arrives(self) -> None:
        """The rain results come in: the rain rows get a new verdict revision."""
        current = (await self("trace/read", project_id="p"))["set_digest"]
        rows = [
            {**row, "base_set_digest": current} for row in export_rows(with_notice(demo_set(2)))
        ]
        await self.load(write_csv(rows), "UPDATE")


async def opened(tmp_path: Path, model: ControlledResearchModel) -> tuple[AppRuntime, Rpc]:
    tmp_path.mkdir(exist_ok=True)
    runtime = await setup(tmp_path, model)
    rpc = Rpc(runtime)
    await rpc.load(export_csv(with_notice(demo_set(1))), "CREATE")
    return runtime, rpc


async def origin_for(rpc: Rpc, subject_id: str, kind: str = "CRITERION") -> dict[str, Any]:
    view = await rpc("trace/read", project_id="p")
    verdict = next(item for item in view["verdicts"] if item["subject_id"] == subject_id)
    return {
        "kind": "TRACE_VERDICT",
        "project_id": "p",
        "subject_kind": kind,
        "subject_id": subject_id,
        "verdict_revision": verdict["revision_digest"],
    }


def with_origin(model: ControlledResearchModel) -> list[dict[str, Any]]:
    """The origin each model call saw, for the calls that had one."""
    return [
        call.context_pack.research_context["origin"]
        for call in model.calls
        if "origin" in call.context_pack.research_context
    ]


async def listed(rpc: Rpc) -> dict[str, Any]:
    threads = (await rpc("thread/list", project_id="p"))["threads"]
    return {item["thread_id"]: item.get("origin") for item in threads}


@pytest.mark.asyncio
async def test_a_wrong_origin_is_refused_with_a_reason_and_starts_nothing(tmp_path: Path) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel())
    try:
        good = await origin_for(rpc, DRY)
        start = {"project_id": "p", "problem": "원인을 찾아 주세요.", "contract_version": 2}
        cases = [
            ("TRACE_ORIGIN_NOT_FOUND", {**good, "subject_id": "NO-SUCH-ROW"}),
            ("TRACE_ORIGIN_NOT_FOUND", {**good, "verdict_revision": "0" * 64}),
            ("TRACE_ORIGIN_PROJECT_MISMATCH", {**good, "project_id": "other"}),
            ("TRACE_ORIGIN_INVALID", {**good, "unexpected": "field"}),
            (
                "TRACE_ORIGIN_INVALID",
                {**good, "state": "PASS_COMPUTED"},
            ),  # the client cannot set facts
        ]
        for reason, origin in cases:
            assert reason in await rpc.error("thread/start", **start, origin=origin)
        assert await listed(rpc) == {}
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_good_origin_adds_context_and_a_result_field_but_no_model_call(
    tmp_path: Path,
) -> None:
    plain_model, origin_model = ControlledResearchModel(), ControlledResearchModel()
    plain_runtime, plain_rpc = await opened(tmp_path / "plain", plain_model)
    runtime, rpc = await opened(tmp_path / "origin", origin_model)
    try:
        question = "지연을 알려 주세요."
        await plain_rpc("thread/start", project_id="p", problem=question, contract_version=2)
        await plain_runtime.bus.drain()
        assert with_origin(plain_model) == []
        origin = await origin_for(rpc, DRY)
        started = await rpc(
            "thread/start", project_id="p", problem=question, contract_version=2, origin=origin
        )
        await runtime.bus.drain()
        assert [c.role for c in origin_model.calls] == [c.role for c in plain_model.calls]
        seen = with_origin(origin_model)
        assert {item["subject_id"] for item in seen} == {DRY}
        # the cause-finding stages (hypotheses, their review, the action plan) see the row
        carrying = {
            c.role for c in origin_model.calls if "origin" in c.context_pack.research_context
        }
        assert carrying >= {
            ModelRole.HYPOTHESIS_GENERATOR,
            ModelRole.HYPOTHESIS_REVIEWER,
            ModelRole.ACTION_PLANNER,
        }
        canonical = seen[0]
        assert canonical["kind"] == "TRACE_VERDICT"
        assert canonical["verdict_revision"] == origin["verdict_revision"]
        assert "note" in canonical  # the model is told the rule decided the verdict
        read = await rpc("thread/read", project_id="p", thread_id=started["thread_id"], view="FULL")
        assert read["current_result"]["result"]["origin"] == canonical
    finally:
        runtime.close()
        plain_runtime.close()


@pytest.mark.asyncio
async def test_the_server_writes_the_verdict_facts_from_the_stored_trace(tmp_path: Path) -> None:
    model = ControlledResearchModel()
    runtime, rpc = await opened(tmp_path, model)
    try:
        origin = await origin_for(rpc, FOG)
        await rpc("thread/start", project_id="p", problem="q", contract_version=2, origin=origin)
        await runtime.bus.drain()
        view = await rpc("trace/read", project_id="p")
        verdict = next(item for item in view["verdicts"] if item["subject_id"] == FOG)
        canonical = with_origin(model)[0]
        assert canonical["state"] == verdict["state"] == "HOLD_NO_RESULT"
        assert canonical["reason_codes"] == list(verdict["reasons"]["computed"])
        assert canonical["trace_set_digest"] == view["set_digest"]
        assert canonical["subject_title"] == "Detection rate in fog"
        assert canonical["rule_summary"]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_the_thread_list_says_which_row_a_thread_belongs_to_and_follows_a_new_revision(
    tmp_path: Path,
) -> None:
    model = ControlledResearchModel()
    runtime, rpc = await opened(tmp_path, model)
    try:
        origin = await origin_for(rpc, RAIN)
        started = await rpc(
            "thread/start", project_id="p", problem="비 줄", contract_version=2, origin=origin
        )
        await runtime.bus.drain()
        other = await rpc(
            "thread/start",
            project_id="p",
            problem="안개 줄",
            contract_version=2,
            origin=await origin_for(rpc, FOG),
        )
        await runtime.bus.drain()
        ordinary = await rpc(
            "thread/start", project_id="p", problem="그냥 질문", contract_version=2
        )
        await runtime.bus.drain()
        found = await listed(rpc)
        assert found[started["thread_id"]] == {
            "subject_kind": "CRITERION",
            "subject_id": RAIN,
            "verdict_revision": origin["verdict_revision"],
        }
        assert found[other["thread_id"]]["subject_id"] == FOG
        assert found[ordinary["thread_id"]] is None  # an ordinary (or old) thread has no origin
        await rpc.rain_arrives()
        newer = await origin_for(rpc, RAIN)
        assert newer["verdict_revision"] != origin["verdict_revision"]
        await rpc(
            "thread/input",
            project_id="p",
            thread_id=started["thread_id"],
            instruction="새 판정으로 다시",
            contract_version=2,
            origin=newer,
        )
        await runtime.bus.drain()
        assert (await listed(rpc))[started["thread_id"]]["verdict_revision"] == (
            newer["verdict_revision"]
        )
        assert with_origin(model)[-1]["verdict_revision"] == newer["verdict_revision"]
        # the old revision is no longer this row's verdict
        assert "TRACE_ORIGIN_REVISION_CHANGED" in await rpc.error(
            "thread/input",
            project_id="p",
            thread_id=started["thread_id"],
            instruction="옛 판정",
            contract_version=2,
            origin=origin,
        )
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_an_origin_only_goes_to_the_thread_of_its_own_row_and_never_through_steer(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel())
    try:
        origin = await origin_for(rpc, RAIN)
        started = await rpc(
            "thread/start", project_id="p", problem="비 줄", contract_version=2, origin=origin
        )
        await runtime.bus.drain()
        ordinary = await rpc(
            "thread/start", project_id="p", problem="그냥 질문", contract_version=2
        )
        await runtime.bus.drain()
        cases = [
            (
                "TRACE_ORIGIN_THREAD_MISMATCH",
                "thread/input",
                started,
                await origin_for(rpc, FOG),
                2,
            ),
            ("TRACE_ORIGIN_THREAD_MISMATCH", "thread/input", ordinary, origin, 2),
            ("TRACE_ORIGIN_NOT_ALLOWED", "thread/steer", started, origin, 2),
        ]
        for reason, method, thread, sent, version in cases:
            message = await rpc.error(
                method,
                project_id="p",
                thread_id=thread["thread_id"],
                instruction="x",
                contract_version=version,
                origin=sent,
            )
            assert reason in message
        # the older contract takes no origin at all: its own input check refuses the unknown key
        with pytest.raises(ValidationError, match="origin"):
            await rpc.raw(
                "thread/input",
                project_id="p",
                thread_id=started["thread_id"],
                instruction="x",
                origin=origin,
            )
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_resend_of_an_accepted_request_returns_the_same_work_after_the_verdict_moved_on(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel())
    try:
        origin = await origin_for(rpc, RAIN)
        params = {"project_id": "p", "problem": "q", "contract_version": 2, "origin": origin}
        message = request("thread/start", "same-key", params)
        first = value(await runtime.bus.dispatch(message))
        await runtime.bus.drain()
        await rpc.rain_arrives()
        again = value(await runtime.bus.dispatch(message))
        assert again["thread_id"] == first["thread_id"]
        assert len(await listed(rpc)) == 1
    finally:
        runtime.close()
