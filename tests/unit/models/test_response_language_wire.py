"""Inspect real adapter serialization with fake HTTP/process transports, never live accounts."""

import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from openai import AsyncOpenAI
from tests.unit.models.test_claude_code import FakeProcess, UsageBoundary, _binary, _connected
from tests.unit.models.test_claude_messages import MODEL, ready_broker, reply, settings
from tests.unit.models.test_oauth_receive_observation import Session, model_request
from tests.unit.models.test_xai_responses import _FixtureSessionReader, _settings
from tests.unit.services.test_research_followup_contract import request_ref

from thoth.adapters.models.anthropic_messages import AnthropicMessagesModel
from thoth.adapters.models.claude_code import ClaudeCodeModel
from thoth.adapters.models.claude_messages import ClaudeMessagesModel
from thoth.adapters.models.codex_http import CodexHttpExecutor
from thoth.adapters.models.codex_oauth import CodexOAuthModel
from thoth.adapters.models.openai_responses import OpenAIResponsesModel
from thoth.adapters.models.xai_model import XaiOAuthModel
from thoth.adapters.models.xai_responses import XaiResponsesExecutor
from thoth.application.services.research_models import ResearchModel
from thoth.domain.enums import ModelRole
from thoth.domain.hypothesis_v4 import HypothesisTableFillV4
from thoth.domain.research_execution import ResearchWork, research_work

pytestmark = pytest.mark.usefixtures("xai_http_guard")


class WireBoundary(UsageBoundary):
    def __init__(self):
        super().__init__()
        self.payloads = []

    def reserve_dispatch(self, dispatch_id, payload, output_tokens, capability):
        self.payloads.append(payload)
        super().reserve_dispatch(dispatch_id, payload, output_tokens, capability)


def request():
    base = model_request()
    return replace(
        base,
        role=ModelRole.HYPOTHESIS_TABLE_FILLER,
        output_model=HypothesisTableFillV4,
        prompt_version="hypothesis_table_filler.v5",
        context_pack=base.context_pack.model_copy(
            update={"problem": "한국어 질문과 「원자료 제목」"}
        ),
    )


def response_body():
    return {
        "id": "resp_synthetic",
        "object": "response",
        "created_at": 1,
        "status": "completed",
        "model": "gpt-test",
        "output": [
            {
                "id": "msg_synthetic",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": '{"tests":[]}', "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": 4,
            "output_tokens": 2,
            "total_tokens": 6,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
        "parallel_tool_calls": False,
        "tools": [],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["codex", "openai", "claude", "xai", "anthropic"])
@pytest.mark.parametrize("language", ["ko", "en"])
async def test_final_http_payload_has_request_language_schema_and_exact_budget_bytes(
    tmp_path: Path, monkeypatch, provider, language
):
    sent = []

    def handler(call: httpx.Request):
        sent.append(call.content)
        if provider == "claude":
            return reply(text='{"tests":[]}')
        if provider == "anthropic":
            return httpx.Response(
                200,
                json={
                    "content": [{"type": "tool_use", "name": "emit_result", "input": {"tests": []}}]
                },
            )
        if provider == "openai":
            return httpx.Response(200, json=response_body())
        event = {"type": "response.completed", "response": response_body()}
        return httpx.Response(200, content=b"data: " + json.dumps(event).encode() + b"\n\n")

    transport = httpx.MockTransport(handler)
    client = None
    selected = None
    if provider == "codex":
        delegate = CodexOAuthModel(
            CodexHttpExecutor(Session(), transport=transport), max_repair_attempts=0
        )
    elif provider == "claude":
        delegate = ClaudeMessagesModel(ready_broker(tmp_path), model=MODEL, transport=transport)
        selected = settings()
    elif provider == "anthropic":
        real_client = httpx.AsyncClient
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda *args, **kwargs: real_client(*args, **{**kwargs, "transport": transport}),
        )
        delegate = AnthropicMessagesModel("synthetic-not-a-key", model_id="claude-test")
    elif provider == "xai":
        delegate = XaiOAuthModel(
            XaiResponsesExecutor(_FixtureSessionReader(), transport=transport),
            max_repair_attempts=0,
        )
        selected = _settings()
    else:
        client = AsyncOpenAI(
            api_key="synthetic-not-a-key",
            base_url="https://example.invalid/v1",
            http_client=httpx.AsyncClient(transport=transport),
        )
        delegate = OpenAIResponsesModel(client, model_id="gpt-test", max_repair_attempts=0)
    boundary = WireBoundary()
    work = ResearchWork(
        request_ref(),
        request().context_pack.problem,
        boundary,
        response_language=language,
        model_settings=selected,
    )
    token = research_work.set(work)
    try:
        result = await ResearchModel(delegate).structured(request())
        assert result.output.tests == ()
    finally:
        research_work.reset(token)
        if client is not None:
            await client.close()
    assert len(sent) == len(boundary.payloads) == 1
    assert len(sent[0]) == len(boundary.payloads[0])
    assert json.loads(sent[0]) == json.loads(boundary.payloads[0])
    wire = sent[0].decode()
    assert f"response language to {'English' if language == 'en' else 'Korean'}" in wire
    assert "한국어 질문" in wire
    assert "knowledge_state" in wire and "UNKNOWN" in wire and '"null"' in wire
    assert "for a Korean question write them in Hangul" not in wire
    assert "program-internal codes" in wire


@pytest.mark.asyncio
async def test_claude_cli_carries_language_in_stdin_and_accounts_for_the_logical_invocation(
    tmp_path: Path,
):
    captured = {}

    class Process(FakeProcess):
        async def communicate(self, input=None):
            captured["stdin"] = input.decode()
            return await super().communicate(input)

    async def spawn(argv, env, cwd):
        captured.update(argv=argv, env=env)
        return Process(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "structured_output": {"tests": []},
            }
        )

    delegate = ClaudeCodeModel(
        tmp_path / "workspace",
        model="sonnet",
        executable=_binary(tmp_path),
        process_factory=spawn,
        status_probe=_connected,
    )
    boundary = WireBoundary()
    token = research_work.set(
        ResearchWork(request_ref(), "한국어 질문", boundary, response_language="en")
    )
    try:
        await ResearchModel(delegate).structured(request())
    finally:
        research_work.reset(token)
    assert len(boundary.payloads) == 1
    logical = json.loads(boundary.payloads[0])
    assert logical["prompt"] == captured["stdin"]
    assert "response language to English" in captured["stdin"]
    assert logical["schema"] == json.loads(
        captured["argv"][captured["argv"].index("--json-schema") + 1]
    )
    assert str(logical["max_output_tokens"]) == captured["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"]
    # The CLI's internal physical API request is not visible to this adapter.
