"""One bounded Claude Messages POST per THOTH model dispatch, with no tool/fallback retry."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import suppress
from pathlib import Path
from typing import TypeVar, cast

import httpx
from pydantic import BaseModel, ValidationError

from thoth.adapters.models.claude_oauth import ClaudeOAuthBroker
from thoth.adapters.models.codex_oauth import (
    constrain_action_families,
    prompt_envelope,
    strict_output_schema,
)
from thoth.adapters.models.reference_schema import (
    apply_hypothesis_review_contract,
    constrain_span_references,
)
from thoth.domain.canonical import canonical_payload, domain_digest, model_digest
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.model_dispatch import ModelControlCapability, ModelReceiveObservation
from thoth.domain.research_execution import (
    check_research_boundary,
    research_work,
    reserve_model_dispatch,
)
from thoth.ports.model import ModelExecutionHold, ModelPort

TModel = TypeVar("TModel", bound=BaseModel)
_ENDPOINT = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"
_MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_OUTPUT_BYTES = 256 * 1024
_MODEL = "claude-sonnet-4-5-20250929"

CLAUDE_MESSAGES_CONTROL = ModelControlCapability(
    capability_id="claude-messages-thoth-oauth-one-post",
    output_control="SERVER_TOKENS",
    native_tools="NONE",
    cancellation="LOCAL_TRANSPORT",
    owns_serialization=True,
)


class ClaudeMessagesHold(ModelExecutionHold):
    pass


async def _finish_shielded[T](task: asyncio.Task[T]) -> T:
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                return task.result()


class ClaudeMessagesModel(ModelPort):
    control_capability = CLAUDE_MESSAGES_CONTROL

    def __init__(
        self,
        broker: ClaudeOAuthBroker,
        *,
        model: str | None,
        transport: httpx.AsyncBaseTransport | None = None,
        endpoint: str = _ENDPOINT,
    ) -> None:
        if model != _MODEL:
            raise ClaudeMessagesHold("CLAUDE_MODEL_UNSUPPORTED")
        if endpoint != _ENDPOINT and transport is None:
            raise ClaudeMessagesHold("CLAUDE_ENDPOINT_OVERRIDE_REQUIRES_SYNTHETIC_TRANSPORT")
        self.broker, self.model, self.transport, self.endpoint = broker, model, transport, endpoint

    async def structured(self, request: ModelRequest[TModel]) -> ModelResult[TModel]:
        started = time.monotonic()
        settings = request.model_settings
        if settings is None or settings.provider != "claude-oauth" or settings.model != self.model:
            raise ClaudeMessagesHold("CLAUDE_MODEL_SETTINGS_MISMATCH")
        # Sonnet 4.5 has no confirmed effort parameter; preserve an explicit
        # selection as a typed HOLD instead of silently dropping/converting it.
        if settings.reasoning_effort is not None:
            raise ClaudeMessagesHold("CLAUDE_EFFORT_UNSUPPORTED")
        if request.max_output_tokens <= 0:
            raise ClaudeMessagesHold("CLAUDE_OUTPUT_LIMIT_INVALID")
        check_research_boundary()
        prompt = prompt_envelope(request)
        schema = constrain_span_references(
            strict_output_schema(request.output_model),
            tuple(span.span_id for span in request.context_pack.evidence),
            request.context_pack.research_context,
        )
        allowed = request.context_pack.policy_hints.get("minimum_action_tier_by_family")
        if isinstance(allowed, dict):
            mapping = cast(dict[object, object], allowed)
            schema = constrain_action_families(schema, tuple(str(key) for key in mapping))
        schema = apply_hypothesis_review_contract(
            schema, request.output_model, request.context_pack.research_context
        )
        output_config: dict[str, object] = {
            "format": {"type": "json_schema", "schema": schema},
        }
        body: dict[str, object] = {
            "model": self.model,
            "max_tokens": request.max_output_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": output_config,
            "stream": False,
        }
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        work = research_work.get()
        deadline = 60.0 if work is None else work.boundary.call_timeout()
        if deadline is None:
            deadline = 60.0
        if deadline <= 0:
            raise ClaudeMessagesHold("CLAUDE_TRANSPORT_DEADLINE")
        remaining = deadline - (time.monotonic() - started)
        if remaining <= 0:
            raise ClaudeMessagesHold("CLAUDE_AUTH_PREPARATION_DEADLINE")
        auth_task = asyncio.create_task(asyncio.to_thread(self.broker.execution_session))
        try:
            session = await asyncio.wait_for(asyncio.shield(auth_task), remaining)
        except (TimeoutError, asyncio.CancelledError) as exc:
            with suppress(Exception):
                await _finish_shielded(auth_task)
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise ClaudeMessagesHold("CLAUDE_AUTH_PREPARATION_DEADLINE") from exc
        remaining = deadline - (time.monotonic() - started)
        if remaining <= 0:
            raise ClaudeMessagesHold("CLAUDE_AUTH_PREPARATION_DEADLINE")
        dispatch_id: str | None = None
        response_status: int | None = None
        response_id: str | None = None
        received_bytes = 0
        visible_bytes = 0
        input_tokens: int | None = None
        output_tokens: int | None = None
        stop_reason = "UNKNOWN"
        cancelled = False
        client = httpx.AsyncClient(
            transport=self.transport,
            follow_redirects=False,
            timeout=httpx.Timeout(connect=3, read=min(remaining, 20), write=3, pool=3),
        )
        stream = client.stream(
            "POST",
            self.endpoint,
            headers={
                "Authorization": "Bearer " + session.access_token,
                "anthropic-version": _API_VERSION,
                "Content-Type": "application/json",
            },
            content=payload,
        )
        entered = False
        try:
            # The cross-process gate spans both reservation and the first physical
            # POST/response headers; a changed generation can send zero bytes.
            async with asyncio.timeout(remaining):
                async with self.broker.dispatch_gate_async(
                    session, timeout_seconds=min(8, remaining)
                ):
                    check_research_boundary()
                    dispatch_id = reserve_model_dispatch(
                        payload, request.max_output_tokens, self.control_capability
                    )
                    first_header_remaining = deadline - (time.monotonic() - started)
                    if first_header_remaining <= 0:
                        raise ClaudeMessagesHold("CLAUDE_TRANSPORT_DEADLINE")
                    response = await asyncio.wait_for(
                        stream.__aenter__(), min(first_header_remaining, 5)
                    )
                    entered = True
            response_status = response.status_code
            remaining = deadline - (time.monotonic() - started)
            if remaining <= 0:
                raise ClaudeMessagesHold("CLAUDE_TRANSPORT_DEADLINE_REMOTE_STOP_UNKNOWN")

            async def read_body() -> bytes:
                nonlocal received_bytes
                collected = bytearray()
                async for chunk in response.aiter_bytes():
                    received_bytes += len(chunk)
                    if received_bytes > _MAX_RESPONSE_BYTES:
                        raise ClaudeMessagesHold("CLAUDE_RESPONSE_TOO_LARGE")
                    collected.extend(chunk)
                return bytes(collected)

            raw = await asyncio.wait_for(read_body(), remaining)
            if response_status != 200:
                reason = (
                    f"CLAUDE_AUTH_REQUIRED_{response_status}"
                    if response_status in {401, 403}
                    else "CLAUDE_RATE_LIMIT_429"
                    if response_status == 429
                    else f"CLAUDE_REQUEST_REJECTED_{response_status}"
                )
                raise ClaudeMessagesHold(reason)
            try:
                envelope: object = json.loads(raw)
            except (ValueError, UnicodeDecodeError) as exc:
                raise ClaudeMessagesHold("CLAUDE_RESPONSE_SCHEMA_UNSUPPORTED") from exc
            if not isinstance(envelope, dict):
                raise ClaudeMessagesHold("CLAUDE_RESPONSE_SCHEMA_UNSUPPORTED")
            data = cast(dict[str, object], envelope)
            identifier = data.get("id")
            response_id = identifier if isinstance(identifier, str) else None
            if data.get("type") != "message" or data.get("model") != self.model:
                raise ClaudeMessagesHold("CLAUDE_RESPONSE_MODEL_MISMATCH")
            usage = data.get("usage")
            if isinstance(usage, dict):
                fields = cast(dict[str, object], usage)
                inbound = fields.get("input_tokens")
                outbound = fields.get("output_tokens")
                input_tokens = inbound if type(inbound) is int else None
                output_tokens = outbound if type(outbound) is int else None
            stop = data.get("stop_reason")
            stop_reason = stop if isinstance(stop, str) else "UNKNOWN"
            blocks = data.get("content")
            if not isinstance(blocks, list) or not blocks:
                raise ClaudeMessagesHold("CLAUDE_OUTPUT_UNAVAILABLE")
            text_parts: list[str] = []
            for block in cast(list[object], blocks):
                if not isinstance(block, dict):
                    raise ClaudeMessagesHold("CLAUDE_NATIVE_TOOL_OR_BLOCK_UNSUPPORTED")
                part = cast(dict[str, object], block)
                if part.get("type") != "text" or not isinstance(part.get("text"), str):
                    raise ClaudeMessagesHold("CLAUDE_NATIVE_TOOL_OR_BLOCK_UNSUPPORTED")
                text_parts.append(cast(str, part["text"]))
            text = "".join(text_parts)
            visible_bytes = len(text.encode())
            if visible_bytes > _MAX_OUTPUT_BYTES:
                raise ClaudeMessagesHold("CLAUDE_OUTPUT_TOO_LARGE")
            if stop_reason != "end_turn":
                raise ClaudeMessagesHold("CLAUDE_OUTPUT_INCOMPLETE")
            try:
                parsed = request.output_model.model_validate_json(text)
            except ValidationError as exc:
                raise ClaudeMessagesHold("CLAUDE_STRUCTURED_OUTPUT_INVALID") from exc
            return ModelResult(
                output=parsed,
                model_id=f"claude-oauth/{self.model}",
                prompt_version=request.prompt_version,
                scripted=False,
                dispatch_ids=(dispatch_id,),
                input_digest=domain_digest(
                    "MODEL_PROMPT_INPUT",
                    "2.0.0",
                    canonical_payload(
                        {
                            "provider": "claude-oauth",
                            "prompt": prompt,
                            "schema": schema,
                            "model": self.model,
                            "effort": settings.reasoning_effort,
                            "prompt_version": request.prompt_version,
                            "model_policy_ref": request.model_policy_ref,
                            "max_output_tokens": request.max_output_tokens,
                            "model_settings": settings,
                        }
                    ),
                ),
                output_digest=model_digest(
                    "MODEL_OUTPUT", cast(BaseModel, parsed), schema_version="1.0.0"
                ),
            )
        except asyncio.CancelledError:
            cancelled = True
            raise
        except httpx.HTTPError as exc:
            raise ClaudeMessagesHold("CLAUDE_TRANSPORT_OUTCOME_UNKNOWN") from exc
        except TimeoutError as exc:
            raise ClaudeMessagesHold("CLAUDE_TRANSPORT_DEADLINE_REMOTE_STOP_UNKNOWN") from exc
        finally:
            try:
                if work is not None and dispatch_id is not None:
                    observation = ModelReceiveObservation(
                        received_bytes=received_bytes,
                        visible_output_bytes=visible_bytes,
                        frame_counts={},
                        max_stream_bytes=_MAX_RESPONSE_BYTES,
                        max_visible_output_bytes=_MAX_OUTPUT_BYTES,
                        timeout_ms=int(deadline * 1000),
                        http_status=response_status,
                        response_id=response_id,
                        local_cancel_requested=cancelled,
                        transport_closed=None,
                    )
                    work.boundary.record_usage(
                        dispatch_id,
                        received_bytes,
                        input_tokens,
                        output_tokens,
                        stop_reason if response_status == 200 else "UNKNOWN",
                        response_id,
                        observation=observation,
                    )
            finally:

                async def close() -> None:
                    if entered:
                        with suppress(Exception):
                            await asyncio.wait_for(stream.__aexit__(None, None, None), 2)
                    with suppress(Exception):
                        await asyncio.wait_for(client.aclose(), 2)

                await _finish_shielded(asyncio.create_task(close()))


def create_claude_oauth_model(
    workspace: Path,
    model: str | None = None,
    *,
    broker: ClaudeOAuthBroker | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ModelPort:
    from thoth.adapters.models.claude_oauth import broker_for_workspace

    return ClaudeMessagesModel(
        broker or broker_for_workspace(workspace), model=model, transport=transport
    )
