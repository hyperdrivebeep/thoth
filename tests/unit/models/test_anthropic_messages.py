from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from thoth.adapters.models.anthropic_messages import AnthropicMessagesModel
from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ModelRole
from thoth.domain.model import ContextPack, ModelRequest


class DemoOutput(DomainModel):
    answer: str


def _request() -> ModelRequest[DemoOutput]:
    return ModelRequest(
        role=ModelRole.SEMANTIC_REVIEWER,
        project_id="project:anthropic",
        cutoff_at=datetime(2026, 9, 20, tzinfo=UTC),
        context_pack=ContextPack(
            case_id="case:anthropic",
            project_id="project:anthropic",
            object_id="object:anthropic",
            problem="이 질문에 답하세요",
            evidence=(),
            criteria=(),
            sufficiency=None,
            input_head_set_digest="a" * 64,
        ),
        output_model=DemoOutput,
        prompt_version="semantic_reviewer.v5",
        model_policy_ref="policy:anthropic",
        max_output_tokens=500,
    )


@pytest.mark.asyncio
async def test_anthropic_calls_static_prompt_helpers_and_emits_structured_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured["timeout"] = kwargs["timeout"]

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: Any) -> httpx.Response:
            captured["url"] = url
            captured["body"] = kwargs["json"]
            return httpx.Response(
                200,
                json={
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "emit_result",
                            "input": {"answer": "한국어 답변"},
                        }
                    ]
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    result = await AnthropicMessagesModel("test-key", model_id="claude-test").structured(_request())

    assert result.output.answer == "한국어 답변"
    assert result.input_digest
    body = captured["body"]
    assert "same language as context_pack.problem" in body["system"]
    assert "이 질문에 답하세요" in body["messages"][0]["content"]
    assert result.prompt_version == "semantic_reviewer.v5"
    assert result.input_digest == domain_digest("MODEL_WIRE", "1.0.0", canonical_payload(body))
