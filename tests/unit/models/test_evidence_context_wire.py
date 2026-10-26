"""Compact quotes survive both provider serialization paths; HTTP is synthetic only."""

import json
from datetime import UTC, datetime
from typing import cast

import httpx
import pytest
from openai import AsyncOpenAI
from tests.unit.models.test_oauth_http_rejection import completed_event
from tests.unit.models.test_oauth_receive_observation import Ping, Session
from tests.unit.test_contextual_retrieval import TEXT, Reader, context, work
from tests.unit.test_text_context_observation import bound, parse_text, spans

from thoth.adapters.models.codex_http import CodexHttpExecutor
from thoth.adapters.models.codex_oauth import CodexOAuthModel
from thoth.adapters.models.openai_responses import OpenAIResponsesModel
from thoth.application.services.research_context_assembler import assemble_context
from thoth.application.services.research_retrieval import work_candidates
from thoth.application.services.research_role_context import bundle_view
from thoth.application.services.retrieval_coverage_view import with_retrieval_coverage
from thoth.domain.enums import ModelRole
from thoth.domain.model import ModelRequest
from thoth.ports.artifact_ledger import ArtifactLedgerPort


@pytest.mark.parametrize("provider", ["codex", "openai"])
async def test_compact_quote_identity_reaches_mock_http(provider: str) -> None:
    document = bound(parse_text(TEXT))
    source = spans(document)
    reader = cast(ArtifactLedgerPort, Reader(document))
    w = work()
    candidates = work_candidates(w, (), source, reader)
    assembly = assemble_context(tuple(s.span_id for s in candidates), candidates, source, reader)
    pack = with_retrieval_coverage(context(source), ModelRole.SEMANTIC_REVIEWER, w)
    pack = pack.model_copy(
        update={
            "research_context": {
                **pack.research_context,
                "context_bundle": bundle_view(assembly),
            }
        }
    )
    request = ModelRequest(
        role=ModelRole.SEMANTIC_REVIEWER,
        project_id=pack.project_id,
        cutoff_at=datetime(2026, 10, 26, tzinfo=UTC),
        context_pack=pack,
        output_model=Ping,
        prompt_version="context-wire-test.v1",
        model_policy_ref="policy:test",
        max_output_tokens=500,
    )
    bodies: list[dict[str, object]] = []

    def reply(incoming: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(incoming.content))
        if provider == "codex":
            return httpx.Response(200, content=completed_event())
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": "test",
                "tools": [],
                "output": [
                    {
                        "id": "msg_test",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": '{"label":"ok"}', "annotations": []}
                        ],
                    }
                ],
            },
        )

    if provider == "codex":
        result = await CodexOAuthModel(
            CodexHttpExecutor(
                Session(),
                transport=httpx.MockTransport(reply),
            )
        ).structured(request)
    else:
        client = AsyncOpenAI(
            api_key="synthetic-not-a-key",
            base_url="https://example.invalid/v1",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(reply)),
        )
        try:
            result = await OpenAIResponsesModel(client, model_id="test").structured(request)
        finally:
            await client.close()
    assert result.output.label == "ok" and len(bodies) == 1
    body = bodies[0]
    raw_input = body["input"]
    if isinstance(raw_input, str):
        prompt = raw_input
    else:
        prompt = json.loads(json.dumps(raw_input))[0]["content"][0]["text"]
    project_raw, remainder = prompt.split("PROJECT_CONTEXT\n", 1)[1].split(
        "\n\nUNTRUSTED_EVIDENCE_SPANS\n", 1
    )
    project = json.loads(project_raw)
    quotes = json.loads(remainder.split("\n\nTASK\n", 1)[0])
    assert "CONTEXT_PROJECTION_V2" in prompt and body["store"] is False
    assert [(s["span_id"], s["exact_text"]) for s in quotes["spans"]] == [
        (s.span_id, s.exact_text) for s in source
    ]
    assert (
        project["research_context"]["retrieval_coverage"]["input_quote_set"]
        == "ALL_SUPPLIED_QUOTES"
    )
    for group in project["research_context"]["context_bundle"]["bundles"]:
        assert all(
            quotes["spans"][i]["span_id"] in {s.span_id for s in source}
            for i in group["quote_refs"]
        )
