from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import TypeVar, cast

import pytest
from pydantic import BaseModel
from qa.scenarios.hero_6g_current_core import run_current_hero
from tests.integration.test_four_projectpack_portability import GenericProjectPackModel

from thoth.domain.action import ActionPlanDraft
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ModelRole
from thoth.domain.model import ModelRequest, ModelResult

TModel = TypeVar("TModel", bound=BaseModel)


class DecimalCostProjectPackModel(GenericProjectPackModel):
    async def structured(self, request: ModelRequest[TModel]) -> ModelResult[TModel]:
        result = await super().structured(request)
        if request.role != ModelRole.ACTION_PLANNER:
            return result
        draft = cast(ActionPlanDraft, result.output)
        first = draft.alternatives[0].model_copy(
            update={"estimated_cost": Decimal("12.50")}
        )
        updated = draft.model_copy(update={"alternatives": (first, *draft.alternatives[1:])})
        return ModelResult(
            output=cast(TModel, updated),
            model_id=result.model_id,
            prompt_version=result.prompt_version,
            scripted=result.scripted,
            input_digest=result.input_digest,
            output_digest=domain_digest(
                "GENERIC_PROJECTPACK_MODEL_OUTPUT",
                "1.0.0",
                canonical_payload(updated),
            ),
        )


@pytest.mark.asyncio
async def test_normal_public_thread_accepts_decimal_model_fields_in_memory_excerpt(
    tmp_path: Path,
) -> None:
    result = await run_current_hero(
        pack_name="public-demo-membrane",
        workspace=tmp_path / "decimal-cost",
        model=DecimalCostProjectPackModel(),
        execution_mode="SEALED_REPLAY",
    )

    assert result.manifest["normal_entry_method"] == "thread/input"
    stages = cast(dict[str, object], result.manifest["stages"])
    memory = cast(dict[str, object], stages["revision_memory_receipt"])
    assert cast(int, memory["committed_memory_count"]) >= 1
    assert memory["semantic_truth_certified"] is False
