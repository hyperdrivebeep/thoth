"""Parse Responses SSE without storing raw error text or credentials."""

from __future__ import annotations

import httpx

from thoth.adapters.models.receive_stats import ReceiveStats
from thoth.adapters.models.response_events import ResponseEvent
from thoth.domain.model_dispatch import ModelTransportReply, PreparedModelDispatch
from thoth.ports.model import ModelExecutionHold


async def parse_responses_sse(
    response: httpx.Response,
    request: PreparedModelDispatch,
    stats: ReceiveStats,
    *,
    reason_prefix: str,
) -> ModelTransportReply:
    buffered = b""
    received = 0
    output = ""
    async for chunk in response.aiter_bytes():
        # HTTPX body chunk before SSE parsing; not raw encrypted/compressed wire bytes.
        stats.received_chunk(chunk)
        stats.last_transport_phase = "READ_BODY"
        if stats.first_byte_ms is None:
            stats.first_byte_ms = stats.elapsed_ms()
        received += len(chunk)
        stats.received_bytes = received
        if request.max_stream_bytes is not None and received > request.max_stream_bytes:
            raise ModelExecutionHold(f"{reason_prefix}_STREAM_BYTE_LIMIT_REMOTE_STOP_UNKNOWN")
        buffered += chunk
        buffered = buffered.replace(b"\r\n", b"\n")
        while b"\n\n" in buffered:
            frame, buffered = buffered.split(b"\n\n", 1)
            if len(frame) > request.max_frame_bytes:
                raise ModelExecutionHold(f"{reason_prefix}_SSE_FRAME_TOO_LARGE")
            data = b"\n".join(
                line[5:].strip() for line in frame.splitlines() if line.startswith(b"data:")
            )
            if not data or data == b"[DONE]":
                continue
            try:
                event = ResponseEvent.model_validate_json(data)
            except (ValueError, TypeError) as exc:
                raise ModelExecutionHold(f"{reason_prefix}_INVALID_SSE") from exc
            kind = event.type
            stats.last_event_type = kind
            if event.response is not None and event.response.id is not None:
                stats.response_id = event.response.id
            stats.frame_counts[kind] = stats.frame_counts.get(kind, 0) + 1
            if kind == "response.output_text.delta":
                output += event.delta
                stats.visible_output_bytes += len(event.delta.encode())
                if (
                    request.max_visible_output_bytes is not None
                    and stats.visible_output_bytes > request.max_visible_output_bytes
                ):
                    raise ModelExecutionHold(
                        f"{reason_prefix}_VISIBLE_BYTE_LIMIT_REMOTE_STOP_UNKNOWN"
                    )
            if kind == "response.output_item.added" and (
                event.item is None or event.item.type not in {"message", "reasoning"}
            ):
                raise ModelExecutionHold(f"{reason_prefix}_UNEXPECTED_TOOL_OUTPUT")
            if kind in {"error", "response.failed", "response.incomplete"}:
                raise ModelExecutionHold(f"{reason_prefix}_INCOMPLETE_RESPONSE")
            if kind == "response.completed":
                completed = event.response
                if completed is None:
                    raise ModelExecutionHold(f"{reason_prefix}_TERMINAL_RESPONSE_MISSING")
                if any(item.type not in {"message", "reasoning"} for item in completed.output):
                    raise ModelExecutionHold(f"{reason_prefix}_UNEXPECTED_TOOL_OUTPUT")
                if not output:
                    output = "".join(
                        part.text
                        for item in completed.output
                        if item.type == "message"
                        for part in item.content
                        if part.type == "output_text"
                    )
                if (
                    request.max_visible_output_bytes is not None
                    and len(output.encode()) > request.max_visible_output_bytes
                ):
                    raise ModelExecutionHold(
                        f"{reason_prefix}_VISIBLE_BYTE_LIMIT_REMOTE_STOP_UNKNOWN"
                    )
                usage = completed.usage
                stats.visible_output_bytes = len(output.encode())
                stats.freeze_terminal()
                cached = None
                if usage is not None and isinstance(usage.input_tokens_details, dict):
                    raw_cached = usage.input_tokens_details.get("cached_tokens")
                    if type(raw_cached) is int and raw_cached >= 0:
                        cached = raw_cached
                return ModelTransportReply(
                    output,
                    received,
                    None if usage is None else usage.input_tokens,
                    None if usage is None else usage.output_tokens,
                    "COMPLETED",
                    completed.id,
                    stats.snapshot(request),
                    cached,
                )
        if len(buffered) > request.max_frame_bytes:
            raise ModelExecutionHold(f"{reason_prefix}_SSE_FRAME_TOO_LARGE")
    raise ModelExecutionHold(f"{reason_prefix}_STREAM_ENDED_WITHOUT_TERMINAL")
