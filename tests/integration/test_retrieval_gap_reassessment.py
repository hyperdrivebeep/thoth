from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import BaseModel
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.domain.canonical import model_digest
from thoth.domain.evidence_requirements import EvidenceRanking, ReviewProposal
from thoth.domain.model import ModelRequest, ModelResult


class ReassessedGapModel(ControlledResearchModel):
    def __init__(self, still_missing: bool) -> None:
        super().__init__()
        self.still_missing = still_missing

    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        result = await super().structured(request)
        output = result.output
        if isinstance(output, EvidenceRanking):
            output = output.model_copy(
                update={"omitted_required_information": ("Header needs checking",)}
            )
        elif isinstance(output, ReviewProposal) and self.still_missing:
            output = output.model_copy(update={"missing_required_scope": ("Header still missing",)})
        converted = request.output_model.model_validate(output.model_dump(mode="python"))
        return replace(
            result,
            output=converted,
            output_digest=model_digest("CONTROLLED_OUTPUT", converted, schema_version="1.0.0"),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("still_missing", [False, True])
async def test_current_semantic_scope_controls_hold_not_old_ranker_warning(
    tmp_path: Path, still_missing: bool
):
    runtime = await setup(tmp_path, ReassessedGapModel(still_missing))
    try:
        admitted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start",
                    {
                        "project_id": "p",
                        "problem": "Check the current record and units",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        status = value(
            await runtime.bus.query(
                request(
                    "thread/read", "read", {"project_id": "p", "thread_id": admitted["thread_id"]}
                )
            )
        )
        result = status["current_result"]["result"]
        assert "retrieval" in result, {
            "failure": status.get("failure"),
            "reason": status["current_result"].get("terminal_reason"),
            "keys": list(result),
        }
        assert result["retrieval"]["omitted_required_information"] == ["Header needs checking"]
        reasons = result["coverage"]["reasons"]
        assert "REQUIRED_CONTEXT_OMITTED" not in reasons
        assert ("REQUIRED_SCOPE_UNEXAMINED" in reasons) == still_missing
    finally:
        runtime.close()
