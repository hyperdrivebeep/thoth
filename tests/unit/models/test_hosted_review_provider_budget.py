from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier

import httpx
import pytest
from openai import AsyncOpenAI
from tests.unit.models.test_openai_responses_model import SHA, DemoOutput

import thoth.apps.hosted_review_composition as hosted_composition
from thoth.adapters.models.openai_responses import OpenAIResponsesModel
from thoth.adapters.storage.hosted_review_budget import (
    HostedReviewProviderBudgetExceeded,
    SqliteHostedReviewProviderBudget,
)
from thoth.application.reducers import SufficiencySignals, assess_information_sufficiency
from thoth.domain.enums import ModelRole
from thoth.domain.model import ContextPack, ModelRequest
from thoth.protocol.jsonrpc import RpcApplicationError


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


def test_provider_budget_is_atomic_across_sessions_and_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "shared" / "provider-budget.sqlite3"
    barrier = Barrier(2)

    def attempt() -> str:
        budget = SqliteHostedReviewProviderBudget(path, max_requests_per_day=1)
        barrier.wait()
        try:
            budget.reserve(now=datetime(2026, 9, 24, tzinfo=UTC))
        except HostedReviewProviderBudgetExceeded:
            return "DENIED"
        return "RESERVED"

    def attempt_for_slot(_slot: int) -> str:
        return attempt()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt_for_slot, range(2)))
    assert sorted(results) == ["DENIED", "RESERVED"]

    reopened = SqliteHostedReviewProviderBudget(path, max_requests_per_day=1)
    with pytest.raises(HostedReviewProviderBudgetExceeded):
        reopened.reserve(now=datetime(2026, 9, 24, tzinfo=UTC))
    reopened.reserve(now=datetime(2026, 9, 25, tzinfo=UTC))
    with pytest.raises(HostedReviewProviderBudgetExceeded):
        reopened.reserve(now=datetime(2026, 9, 25, tzinfo=UTC))


@pytest.mark.asyncio
async def test_provider_budget_reserves_each_repair_before_network(tmp_path: Path) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "id": f"resp_{calls}",
                "object": "response",
                "created_at": calls,
                "status": "completed",
                "model": "gpt-test",
                "output": [
                    {
                        "id": f"msg_{calls}",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "refusal", "refusal": "invalid structured output"}],
                    }
                ],
            },
        )

    budget = SqliteHostedReviewProviderBudget(
        tmp_path / "shared" / "provider-budget.sqlite3", max_requests_per_day=2
    )
    client = AsyncOpenAI(
        api_key="test-not-a-real-key",
        base_url="https://example.invalid/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        model = OpenAIResponsesModel(
            client, model_id="gpt-test", before_request=budget.reserve
        )
        with pytest.raises(HostedReviewProviderBudgetExceeded):
            await model.structured(_request())
    finally:
        await client.close()
    assert calls == 2


@pytest.mark.asyncio
async def test_provider_budget_denial_or_storage_failure_sends_nothing(tmp_path: Path) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    client = AsyncOpenAI(
        api_key="test-not-a-real-key",
        base_url="https://example.invalid/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        denied = SqliteHostedReviewProviderBudget(
            tmp_path / "denied.sqlite3", max_requests_per_day=0
        )
        with pytest.raises(HostedReviewProviderBudgetExceeded):
            await OpenAIResponsesModel(
                client, model_id="gpt-test", before_request=denied.reserve
            ).structured(_request())

        directory = tmp_path / "not-a-database"
        directory.mkdir()
        broken = SqliteHostedReviewProviderBudget(directory, max_requests_per_day=1)
        with pytest.raises(sqlite3.OperationalError):
            await OpenAIResponsesModel(
                client, model_id="gpt-test", before_request=broken.reserve
            ).structured(_request())
    finally:
        await client.close()
    assert calls == 0


@pytest.mark.asyncio
async def test_hosted_resolvers_share_provider_limit_before_fake_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    clients: list[AsyncOpenAI] = []
    retry_settings: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": "gpt-5.5",
                "output": [
                    {
                        "id": "msg_test",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"label":"ok","count":1}',
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "parallel_tool_calls": True,
                "tools": [],
            },
        )

    real_client = AsyncOpenAI

    def fake_client(*, api_key: str, base_url: str, max_retries: int) -> AsyncOpenAI:
        retry_settings.append(max_retries)
        client = real_client(
            api_key=api_key,
            base_url=base_url,
            max_retries=max_retries,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        clients.append(client)
        return client

    monkeypatch.setenv("THOTH_WORKSPACE", str(tmp_path / "shared-origin"))
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-real-key")
    monkeypatch.setenv("HOSTED_REVIEW_MAX_OPENAI_REQUESTS_PER_DAY", "1")
    monkeypatch.delenv("HOSTED_REVIEW_DISPATCH_GATE", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setattr(hosted_composition, "AsyncOpenAI", fake_client)
    try:
        session_a = hosted_composition.hosted_openai_resolver().resolve(
            provider="openai", model="gpt-5.5"
        )
        session_b = hosted_composition.hosted_openai_resolver().resolve(
            provider="openai", model="gpt-5.5"
        )
        result = await session_a.structured(_request())
        assert result.output.label == "ok"
        with pytest.raises(RpcApplicationError, match="HOSTED_REVIEW_OPENAI_LIMIT"):
            await session_b.structured(_request())
    finally:
        for client in clients:
            await client.close()
    assert calls == 1
    assert retry_settings == [0, 0]
