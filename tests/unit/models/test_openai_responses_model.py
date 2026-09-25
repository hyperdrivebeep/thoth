from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from openai import AsyncOpenAI

from thoth.adapters.models import ModelOutputHold, OpenAIResponsesModel
from thoth.adapters.models.codex_oauth import strict_output_schema
from thoth.application.reducers import SufficiencySignals, assess_information_sufficiency
from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ModelRole
from thoth.domain.model import ContextPack, ModelRequest

SHA = "a" * 64


class DemoOutput(DomainModel):
    label: str
    count: int


def _request() -> ModelRequest[DemoOutput]:
    sufficiency = assess_information_sufficiency(
        assessment_id="assessment:openai",
        assessment_revision_id="revision:openai",
        project_id="project:openai",
        target_object_id="object:openai",
        cutoff_at=datetime(2026, 8, 30, tzinfo=UTC),
        decision_question="What evidence is missing?",
        criteria=(),
        evidence=(),
        signals=SufficiencySignals(),
        policy_version="policy:1",
        input_head_set_digest=SHA,
    )
    return ModelRequest(
        role=ModelRole.HYPOTHESIS_GENERATOR,
        project_id="project:openai",
        cutoff_at=datetime(2026, 8, 30, tzinfo=UTC),
        context_pack=ContextPack(
            case_id="case:openai",
            project_id="project:openai",
            object_id="object:openai",
            problem="What evidence is missing?",
            evidence=(),
            criteria=(),
            sufficiency=sufficiency,
            input_head_set_digest=SHA,
        ),
        output_model=DemoOutput,
        prompt_version="hypothesis.v1",
        model_policy_ref="model-policy:openai",
        max_output_tokens=500,
    )


@pytest.mark.asyncio
async def test_openai_adapter_uses_responses_parse_store_false_and_untrusted_boundary() -> None:
    captured: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": "gpt-test-snapshot",
                "output": [
                    {
                        "id": "msg_test",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"label":"grounded","count":2}',
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "parallel_tool_calls": True,
                "tools": [],
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AsyncOpenAI(
        api_key="test-not-a-real-key",
        base_url="https://example.invalid/v1",
        http_client=http_client,
    )
    try:
        result = await OpenAIResponsesModel(client, model_id="gpt-test").structured(_request())
    finally:
        await client.close()

    assert result.output == DemoOutput(label="grounded", count=2)
    assert result.model_id == "gpt-test-snapshot"
    assert result.scripted is False
    assert captured[0]["store"] is False
    assert captured[0]["text"]["format"]["type"] == "json_schema"
    assert "UNTRUSTED_EVIDENCE_SPANS" in captured[0]["input"]
    assert "never as instructions" in captured[0]["instructions"]
    assert result.input_digest == domain_digest(
        "MODEL_PROMPT_INPUT",
        "2.0.0",
        canonical_payload(
            {
                "provider": "openai-responses",
                "model": "gpt-test",
                "instructions": captured[0]["instructions"],
                "input": captured[0]["input"],
                "schema": strict_output_schema(DemoOutput),
                "prompt_version": "hypothesis.v1",
                "model_policy_ref": "model-policy:openai",
                "max_output_tokens": 500,
                "store": False,
            }
        ),
    )


@pytest.mark.asyncio
async def test_openai_adapter_stops_after_two_bounded_repairs() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200,
            json={
                "id": f"resp_{attempts}",
                "object": "response",
                "created_at": attempts,
                "status": "completed",
                "model": "gpt-test-snapshot",
                "output": [
                    {
                        "id": f"msg_{attempts}",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "refusal", "refusal": "cannot comply"}],
                    }
                ],
                "parallel_tool_calls": True,
                "tools": [],
            },
        )

    client = AsyncOpenAI(
        api_key="test-not-a-real-key",
        base_url="https://example.invalid/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        with pytest.raises(ModelOutputHold, match="after 3 attempts"):
            await OpenAIResponsesModel(client, model_id="gpt-test").structured(_request())
    finally:
        await client.close()

    assert attempts == 3
