"""Anthropic Messages JSON via tool schema. Secrets stay in the caller."""

from __future__ import annotations

from typing import TypeVar, cast

import httpx
from pydantic import BaseModel

from thoth.adapters.models.codex_oauth import strict_output_schema
from thoth.adapters.models.openai_responses import ModelOutputHold, OpenAIResponsesModel
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.model_dispatch import ModelControlCapability
from thoth.domain.research_execution import research_work, reserve_model_dispatch
from thoth.ports.model import ModelPort

TModel = TypeVar("TModel", bound=BaseModel)


class AnthropicMessagesModel(ModelPort):
    control_capability = ModelControlCapability(
        capability_id="anthropic-messages-json-tool-v1",
        output_control="SERVER_TOKENS",
        native_tools="NONE",
        cancellation="LOCAL_TRANSPORT",
        owns_serialization=True,
    )

    def __init__(self, api_key: str, *, model_id: str) -> None:
        self._api_key = api_key
        self._model_id = model_id

    async def structured(self, request: ModelRequest[TModel]) -> ModelResult[TModel]:
        prompt = OpenAIResponsesModel.input_envelope(request)
        schema = strict_output_schema(request.output_model)
        body = {
            "model": self._model_id,
            "max_tokens": request.max_output_tokens or 4096,
            "system": OpenAIResponsesModel.instructions(request),
            "messages": [{"role": "user", "content": prompt}],
            "tools": [
                {
                    "name": "emit_result",
                    "description": "Return the structured result",
                    "input_schema": schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": "emit_result"},
        }
        work = research_work.get()
        dispatch_id: str | None = None
        if work is not None:
            wire = httpx.Request("POST", "https://api.anthropic.com/v1/messages", json=body).content
            dispatch_id = reserve_model_dispatch(
                wire, request.max_output_tokens, self.control_capability
            )
        timeout = None if work is None else work.boundary.call_timeout()
        async with httpx.AsyncClient(timeout=timeout or 120.0) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=body,
            )
        if work is not None and dispatch_id is not None:
            work.boundary.record_usage(
                dispatch_id,
                len(response.content),
                None,
                None,
                "COMPLETED" if response.status_code == 200 else "UNKNOWN",
                None,
            )
        if response.status_code >= 400:
            raise ModelOutputHold(f"ANTHROPIC_HTTP_{response.status_code}")
        raw_payload = cast(object, response.json())
        payload = cast(dict[str, object], raw_payload) if isinstance(raw_payload, dict) else {}
        content = payload.get("content")
        blocks = cast(list[object], content) if isinstance(content, list) else []
        tool: object | None = None
        for raw_block in blocks:
            if not isinstance(raw_block, dict):
                continue
            block = cast(dict[str, object], raw_block)
            if block.get("type") == "tool_use":
                tool = block.get("input")
                break
        if not isinstance(tool, dict):
            raise ModelOutputHold("ANTHROPIC_STRUCTURED_UNAVAILABLE")
        parsed = request.output_model.model_validate(tool)
        from thoth.domain.canonical import canonical_payload, domain_digest, model_digest

        return ModelResult(
            output=parsed,
            model_id=self._model_id,
            prompt_version=request.prompt_version,
            scripted=False,
            input_digest=domain_digest("MODEL_WIRE", "1.0.0", canonical_payload(body)),
            output_digest=model_digest("MODEL_OUTPUT", parsed, schema_version="1.0.0"),
        )
