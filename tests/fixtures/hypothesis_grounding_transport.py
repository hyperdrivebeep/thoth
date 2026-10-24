"""Real HTTP payload preparation with a dummy session and zero-network dispatch."""

import json
from typing import Any

from pydantic import BaseModel

from thoth.adapters.models.codex_http import CodexHttpExecutor
from thoth.adapters.models.codex_oauth import CodexOAuthModel
from thoth.domain.model import ModelRequest
from thoth.domain.model_dispatch import ModelTransportReply, OAuthSession, PreparedModelDispatch


class DummySession:
    def read(self) -> OAuthSession:
        return OAuthSession("fixture-token-not-real", "fixture-account", "fixture-model", "medium")


class OfflineWireExecutor(CodexHttpExecutor):
    def __init__(self, response: str) -> None:
        super().__init__(DummySession())
        self.response = response
        self.dispatches: list[PreparedModelDispatch] = []

    async def dispatch(self, request: PreparedModelDispatch) -> ModelTransportReply:
        self.dispatches.append(request)
        return ModelTransportReply(
            text=self.response, received_bytes=len(self.response.encode()), remote_stop="COMPLETED"
        )


async def measured_wire[T: BaseModel](request: ModelRequest[T], output: T) -> dict[str, Any]:
    response = output.model_dump_json()
    executor = OfflineWireExecutor(response)
    result = await CodexOAuthModel(executor).structured(request)
    assert len(executor.dispatches) == 1
    prepared = executor.dispatches[0]
    body = json.loads(prepared.payload)
    assert body["tools"] == [] and body["tool_choice"] == "none"
    assert body["text"]["format"]["strict"] is True
    assert type(result.output) is request.output_model
    return {
        "prompt_version": request.prompt_version,
        "serialized_request_bytes": len(prepared.payload),
        "structured_output_bytes": len(response.encode()),
        "output_tokens_reserved": prepared.output_tokens_reserved,
        "provider_output_control": prepared.capability.output_control,
        "remote_max_output_tokens_present": "max_output_tokens" in body,
        "model_input_digest": result.input_digest,
        "input_tokens": None,
        "output_tokens": None,
        "network_calls": 0,
        "assumption": "Synthetic no-network response; exact serialization, not billed token usage.",
    }
