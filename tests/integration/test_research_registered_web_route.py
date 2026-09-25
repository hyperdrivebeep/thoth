"""A registered web route can fill a local-coverage gap without becoming a search engine."""

from __future__ import annotations

from pathlib import Path
from typing import Self

import pytest
from pydantic import BaseModel
from tests.integration.public_web_test_support import (
    GPU_LOG_HTML,
    LISTING_URI,
    PAPER_URI,
    RecordingPublicReader,
    artifact_uris,
    connect_html,
    create_web_project,
    finished_result,
    record,
    rpc_value,
    web_runtime,
)
from tests.integration.storage_coverage_helpers import request
from tests.integration.test_research_request_v2 import ControlledResearchModel

from thoth.domain.canonical import model_digest
from thoth.domain.enums import ModelRole
from thoth.domain.evidence_requirements import ResearchSourcePlan, SourceSelector
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.public_web_access import PROJECT_PUBLIC_WEB_CONNECTOR_ID


class RegisteredWebRouteModel(ControlledResearchModel):
    def __init__(self, *, connected_only: bool = False) -> None:
        super().__init__(missing=True, discover=True)
        self.connected_only = connected_only

    def resolve(self, *, provider: str, model: str | None = None) -> Self:
        del provider, model
        return self

    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        result = await super().structured(request)
        if request.role == ModelRole.RESEARCH_PLANNER:
            output = result.output.model_copy(
                update={"connected_sources_only": self.connected_only}
            )
            return ModelResult(
                output=output,
                model_id=result.model_id,
                scripted=True,
                prompt_version=result.prompt_version,
                input_digest=result.input_digest,
                output_digest=model_digest("OUTPUT", output, schema_version="1.0.0"),
            )
        if request.role == ModelRole.SOURCE_PLANNER:
            output = ResearchSourcePlan(
                reason="Use the registered arXiv listing, not a query",
                selectors=(
                    SourceSelector.model_validate(
                        {
                            "connector_id": PROJECT_PUBLIC_WEB_CONNECTOR_ID,
                            "selector": {
                                "mode": "SITE_DISCOVER",
                                "entrypoint_id": "arxiv:cs.LG:recent",
                            },
                        }
                    ),
                ),
            )
            parsed = request.output_model.model_validate(output.model_dump())
            return ModelResult(
                output=parsed,
                model_id=result.model_id,
                scripted=True,
                prompt_version=result.prompt_version,
                input_digest=result.input_digest,
                output_digest=model_digest("OUTPUT", parsed, schema_version="1.0.0"),
            )
        return result


@pytest.mark.asyncio
async def test_local_gpu_log_does_not_block_registered_web_acquisition(tmp_path: Path) -> None:
    reader = RecordingPublicReader()
    model = RegisteredWebRouteModel()
    runtime = web_runtime(tmp_path, reader, model_resolver=model)
    try:
        await create_web_project(runtime)
        await connect_html(runtime, tmp_path, "gpu-idle.html", GPU_LOG_HTML)
        result = await finished_result(runtime)
        discovery = record(result["discovery"])
        assert discovery["acquired_count"] == 1
        assert discovery["entire_internet_searched"] is False
        assert discovery["discovery_kind"] == "REGISTERED_SITE_ENUMERATION"
        assert reader.uris == [LISTING_URI, PAPER_URI]
        listed = rpc_value(
            await runtime.bus.dispatch(request("project/source/list", "after", {"project_id": "p"}))
        )
        uris = artifact_uris(listed)
        assert PAPER_URI in uris
        assert LISTING_URI not in uris
        assert any(call.role == ModelRole.SOURCE_PLANNER for call in model.calls)
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_connected_only_request_makes_no_public_web_calls(tmp_path: Path) -> None:
    reader = RecordingPublicReader()
    model = RegisteredWebRouteModel(connected_only=True)
    runtime = web_runtime(tmp_path, reader, model_resolver=model)
    try:
        await create_web_project(runtime)
        await connect_html(runtime, tmp_path, "gpu-idle.html", GPU_LOG_HTML)
        result = await finished_result(runtime)
        assert record(result["discovery"])["state"] == "SKIPPED_REQUEST_SCOPE"
        assert reader.uris == []
        assert all(call.role != ModelRole.SOURCE_PLANNER for call in model.calls)
    finally:
        runtime.close()
