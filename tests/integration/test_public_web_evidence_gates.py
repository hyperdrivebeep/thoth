"""Acquired public pages keep cutoff gates; GPU/paper mismatch stays PARTIAL_HOLD."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.integration.public_web_test_support import (
    GPU_LOG_HTML,
    PAPER_URI,
    RecordingPublicReader,
    connect_html,
    create_web_project,
    finished_result,
    integer,
    items,
    record,
    rpc_value,
    text,
    web_runtime,
)
from tests.integration.storage_coverage_helpers import request
from tests.integration.test_research_registered_web_route import RegisteredWebRouteModel

AFTER_CUTOFF_HTML = (
    "<html><head><meta property='article:published_time' "
    "content='2026-09-25T00:00:00Z' /></head>"
    "<body><p>This note was written after the project cutoff.</p></body></html>"
)
ELIGIBLE_GPU_LOG_HTML = GPU_LOG_HTML.replace(
    "<body>",
    "<body><article itemscope itemtype='https://schema.org/Article'>"
    "<meta property='article:published_time' content='2026-09-01T00:00:00Z' />",
).replace("</body>", "</article></body>")


@pytest.mark.asyncio
async def test_acquired_web_page_stays_unknown_and_out_of_eligible_ranking(
    tmp_path: Path,
) -> None:
    reader = RecordingPublicReader()
    runtime = web_runtime(tmp_path, reader, model_resolver=RegisteredWebRouteModel())
    try:
        await create_web_project(runtime)
        await connect_html(runtime, tmp_path, "gpu-idle.html", ELIGIBLE_GPU_LOG_HTML)
        await connect_html(
            runtime,
            tmp_path,
            "after.html",
            AFTER_CUTOFF_HTML,
            cutoff_state="AFTER_CUTOFF",
        )
        before = rpc_value(
            await runtime.bus.dispatch(
                request("project/source/list", "before-research", {"project_id": "p"})
            )
        )
        anchor = next(
            record(item)
            for item in items(before["artifacts"])
            if text(record(item)["source_uri"]).endswith("gpu-idle.html")
        )
        assert anchor["cutoff_state"] == "ELIGIBLE"
        result = await finished_result(runtime)
        listed = rpc_value(
            await runtime.bus.dispatch(
                request("project/source/list", "sources", {"project_id": "p"})
            )
        )
        artifacts = {
            text(record(item)["source_uri"]): record(item) for item in items(listed["artifacts"])
        }
        assert artifacts[PAPER_URI]["cutoff_state"] == "UNKNOWN_TIME"
        after = next(
            record(item)
            for item in items(listed["artifacts"])
            if text(record(item)["source_uri"]).endswith("after.html")
        )
        assert after["cutoff_state"] == "AFTER_CUTOFF"
        eligible = {
            text(record(item)["source_uri"])
            for item in items(listed["artifacts"])
            if record(item)["cutoff_state"] == "ELIGIBLE"
        }
        assert PAPER_URI not in eligible
        assert not any(uri.endswith("after.html") for uri in eligible)
        retrieval = record(result["retrieval"])
        assert integer(retrieval["candidate_count"]) <= integer(retrieval["catalog_span_count"])
        packet = record(result["source_packet"])
        evidence_uris = [text(record(item)["source_uri"]) for item in items(packet["artifacts"])]
        assert PAPER_URI not in evidence_uris
        assert not any(uri.endswith("after.html") for uri in evidence_uris)
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_no_eligible_source_keeps_unknown_fallback_and_explicit_limitation(
    tmp_path: Path,
) -> None:
    reader = RecordingPublicReader()
    model = RegisteredWebRouteModel()
    runtime = web_runtime(tmp_path, reader, model_resolver=model)
    try:
        await create_web_project(runtime)
        await connect_html(runtime, tmp_path, "gpu-idle.html", GPU_LOG_HTML)
        await connect_html(
            runtime,
            tmp_path,
            "after.html",
            AFTER_CUTOFF_HTML,
            cutoff_state="AFTER_CUTOFF",
        )
        await connect_html(
            runtime,
            tmp_path,
            "prohibited.html",
            "<html><body><p>Restricted fixture</p></body></html>",
            cutoff_state="PROHIBITED_CONTEXT",
        )
        result = await finished_result(runtime)
        listed = rpc_value(
            await runtime.bus.dispatch(
                request("project/source/list", "fallback-sources", {"project_id": "p"})
            )
        )
        states = {
            text(artifact["source_uri"]): text(artifact["cutoff_state"])
            for item in items(listed["artifacts"])
            for artifact in (record(item),)
        }
        assert not any(state == "ELIGIBLE" for state in states.values())
        assert states[PAPER_URI] == "UNKNOWN_TIME"
        assert any(
            uri.endswith("gpu-idle.html") and state == "UNKNOWN_TIME"
            for uri, state in states.items()
        )
        assert any(
            uri.endswith("after.html") and state == "AFTER_CUTOFF"
            for uri, state in states.items()
        )
        assert any(
            uri.endswith("prohibited.html") and state == "PROHIBITED_CONTEXT"
            for uri, state in states.items()
        )
        packet = record(result["source_packet"])
        packet_uris = [text(record(item)["source_uri"]) for item in items(packet["artifacts"])]
        assert PAPER_URI in packet_uris
        assert any(uri.endswith("gpu-idle.html") for uri in packet_uris)
        assert all(states[uri] == "UNKNOWN_TIME" for uri in packet_uris)
        assert not any(uri.endswith("after.html") for uri in packet_uris)
        assert not any(uri.endswith("prohibited.html") for uri in packet_uris)
        assert result["source_time_limitation"] == "SOURCE_TIME_UNCONFIRMED"
        assert model.calls
        assert all(
            call.context_pack.research_context.get("source_time_limitation")
            == "SOURCE_TIME_UNCONFIRMED"
            for call in model.calls
        )
        assert result["answer_status"] == "PARTIAL_HOLD"
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_web_route_does_not_turn_gpu_paper_mismatch_into_pass(tmp_path: Path) -> None:
    reader = RecordingPublicReader()
    runtime = web_runtime(tmp_path, reader, model_resolver=RegisteredWebRouteModel())
    try:
        await create_web_project(runtime)
        await connect_html(runtime, tmp_path, "gpu-idle.html", GPU_LOG_HTML)
        result = await finished_result(runtime)
        assert result["answer_status"] == "PARTIAL_HOLD"
        assert record(result["discovery"])["acquired_count"] == 1
        assert "검증 보류" in text(result["answer"])
    finally:
        runtime.close()
