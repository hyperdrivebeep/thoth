"""A thread read judges currentness from the result's own sources, not by loading every span."""

from __future__ import annotations

from pathlib import Path
from types import MethodType
from typing import Any, cast

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.application.commands.research_threads import ResearchThreadHandlers
from thoth.protocol.registry import MethodRegistry


def thread_handlers(runtime: Any) -> ResearchThreadHandlers:
    registry = cast(MethodRegistry, vars(runtime.bus)["_registry"])
    return cast(ResearchThreadHandlers, cast(MethodType, registry.resolve("thread/read")).__self__)


async def read(runtime: Any, key: str, thread: str) -> dict[str, Any]:
    return value(
        await runtime.bus.query(
            request("thread/read", key, {"project_id": "p", "thread_id": thread})
        )
    )


@pytest.mark.asyncio
async def test_a_current_result_is_read_without_loading_every_evidence_span(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
        artifacts: Any = thread_handlers(runtime).analysis.artifacts
        loads = {"count": 0}
        original = artifacts.list_evidence

        def counting(*args: Any, **kwargs: Any) -> Any:
            loads["count"] += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(artifacts, "list_evidence", counting)
        status = await read(runtime, "r1", started["thread_id"])
        assert (
            status["current_result"] is not None
            and status["basis_currentness"]["state"] == "CURRENT"
        )
        assert status["freshness"] == "CURRENT"
        assert loads["count"] == 0
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_result_still_turns_stale_when_its_source_is_disconnected(tmp_path: Path) -> None:
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
        before = await read(runtime, "r1", started["thread_id"])
        assert before["freshness"] == "CURRENT" and before["current_result"] is not None
        sources = value(
            await runtime.bus.query(request("project/source/list", "l", {"project_id": "p"}))
        )
        for binding in sources["bindings"]:
            value(
                await runtime.bus.dispatch(
                    request(
                        "project/source/disconnect",
                        f"d-{binding['binding_id']}",
                        {"project_id": "p", "binding_id": binding["binding_id"], "mode": "REVOKE"},
                    )
                )
            )
        after = await read(runtime, "r2", started["thread_id"])
        assert after["freshness"] != "CURRENT"
        assert after["current_result"] is None and after["previous_result"] is not None
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_stored_structure_is_digested_once_and_the_digest_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
        status = await read(runtime, "r1", started["thread_id"])
        analysis = thread_handlers(runtime).analysis
        spans = analysis.evidence_by_ids("p", status["current_result"]["source_refs"])
        assert spans
        analysis._structure_summaries.clear()  # pyright: ignore[reportPrivateUsage]
        cold = analysis.source_digest(spans)
        reads = {"count": 0}
        original = analysis.artifacts.read_structure

        def counting(*args: Any, **kwargs: Any) -> Any:
            reads["count"] += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(analysis.artifacts, "read_structure", counting)
        warm = analysis.source_digest(spans)
        assert warm == cold and reads["count"] == 0
        analysis._structure_summaries.clear()  # pyright: ignore[reportPrivateUsage]
        assert analysis.source_digest(spans) == cold and reads["count"] > 0
        # the stored result's own digest is the one this computation reproduces
        assert status["current_result"]["source_context_digest"] == analysis.source_digest(
            spans, version=status["current_result"].get("source_context_version") or "2.1.0"
        )
    finally:
        runtime.close()
