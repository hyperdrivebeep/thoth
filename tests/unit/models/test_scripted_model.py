from __future__ import annotations

from datetime import UTC, datetime

import pytest

from thoth.adapters.models import ScriptedFixtureMissing, ScriptedModel
from thoth.application.reducers import SufficiencySignals, assess_information_sufficiency
from thoth.domain.base import DomainModel
from thoth.domain.enums import ModelRole
from thoth.domain.model import ContextPack, ModelRequest

SHA = "a" * 64


class DemoOutput(DomainModel):
    label: str
    count: int


def _request(prompt_version: str = "hypothesis.v1") -> ModelRequest[DemoOutput]:
    sufficiency = assess_information_sufficiency(
        assessment_id="assessment:1",
        assessment_revision_id="revision:1",
        project_id="project:1",
        target_object_id="object:1",
        cutoff_at=datetime(2026, 8, 30, tzinfo=UTC),
        decision_question="왜 결과가 목표에 미달했는가?",
        criteria=(),
        evidence=(),
        signals=SufficiencySignals(),
        policy_version="policy:1",
        input_head_set_digest=SHA,
    )
    context = ContextPack(
        case_id="case:scripted",
        project_id="project:1",
        object_id="object:1",
        problem="result is below target",
        evidence=(),
        criteria=(),
        sufficiency=sufficiency,
        input_head_set_digest=SHA,
    )
    return ModelRequest(
        role=ModelRole.HYPOTHESIS_GENERATOR,
        project_id="project:1",
        cutoff_at=datetime(2026, 8, 30, tzinfo=UTC),
        context_pack=context,
        output_model=DemoOutput,
        prompt_version=prompt_version,
        model_policy_ref="model-policy:scripted-only",
        max_output_tokens=500,
    )


@pytest.mark.asyncio
async def test_scripted_model_is_deterministic_and_explicitly_badged() -> None:
    model = ScriptedModel(
        {("case:scripted", "HYPOTHESIS_GENERATOR", "hypothesis.v1"): {"label": "ok", "count": 3}}
    )

    first = await model.structured(_request())
    second = await model.structured(_request())

    assert first == second
    assert first.model_id == "SCRIPTED_MODEL"
    assert first.scripted is True
    assert first.output == DemoOutput(label="ok", count=3)


@pytest.mark.asyncio
async def test_scripted_model_refuses_missing_fixture() -> None:
    model = ScriptedModel({})

    with pytest.raises(ScriptedFixtureMissing):
        await model.structured(_request("missing.v1"))
