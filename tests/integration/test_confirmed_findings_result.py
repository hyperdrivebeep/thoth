"""A result carries the reviewer's confirmed findings, minus those citing unsupplied sources."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.domain.canonical import model_digest
from thoth.domain.enums import ModelRole
from thoth.domain.evidence_requirements import ConfirmedFinding, ReviewProposal
from thoth.domain.model import ModelRequest, ModelResult


class FindingModel(ControlledResearchModel):
    async def structured(self, request: ModelRequest[Any]) -> ModelResult[Any]:  # pyright: ignore[reportIncompatibleMethodOverride]
        result = await super().structured(request)
        if request.role != ModelRole.SEMANTIC_REVIEWER:
            return result
        spans = [s.span_id for s in request.context_pack.evidence]
        proposal = ReviewProposal.model_validate(result.output.model_dump()).model_copy(
            update={
                "confirmed_findings": (
                    ConfirmedFinding(
                        statement="기록 LAB-42의 지연은 12 ms로 적혀 있다",
                        evidence_refs=(spans[0],),
                        requirement_id=None,
                        kind="DOCUMENT_FACT",
                    ),
                    ConfirmedFinding(
                        statement="다른 조건의 재현 기록은 찾아봤지만 없다",
                        evidence_refs=(spans[0],),
                        requirement_id=None,
                        kind="VALID_NEGATIVE_FINDING",
                    ),
                    ConfirmedFinding(
                        statement="제공되지 않은 자료를 인용한 사실",
                        evidence_refs=(spans[0], "span:never-supplied"),
                        requirement_id=None,
                        kind="DOCUMENT_FACT",
                    ),
                )
            }
        )
        return replace(
            result,
            output=proposal,
            output_digest=model_digest("OUTPUT", proposal, schema_version="1.0.0"),
        )


@pytest.mark.asyncio
async def test_findings_reach_the_stored_result_and_the_unsupplied_citation_is_counted(
    tmp_path: Path,
) -> None:
    runtime = await setup(tmp_path, FindingModel())
    try:
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
        status = value(
            await runtime.bus.dispatch(
                request(
                    "thread/read", "read", {"project_id": "p", "thread_id": accepted["thread_id"]}
                )
            )
        )
        result = status["current_result"]["result"]
        assert [item["kind"] for item in result["confirmed_findings"]] == [
            "DOCUMENT_FACT",
            "VALID_NEGATIVE_FINDING",
        ]
        assert all(item["evidence_refs"] for item in result["confirmed_findings"])
        assert result["confirmed_findings_dropped"] == 1
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_model_that_returns_no_findings_leaves_an_empty_list(tmp_path: Path) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel())
    try:
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
        status = value(
            await runtime.bus.dispatch(
                request(
                    "thread/read", "read", {"project_id": "p", "thread_id": accepted["thread_id"]}
                )
            )
        )
        result = status["current_result"]["result"]
        assert result["confirmed_findings"] == []
        assert result["confirmed_findings_dropped"] == 0
    finally:
        runtime.close()
