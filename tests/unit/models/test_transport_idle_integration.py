import asyncio
import json
from contextlib import suppress
from decimal import Decimal
from time import monotonic

import httpx
import pytest
from tests.unit.models.test_transport_phase_contract import Session

from thoth.adapters.models.codex_http import CodexHttpExecutor
from thoth.domain.model_dispatch import TransportTimeouts
from thoth.ports.model import ModelTransportHold


class LocalTransport(httpx.AsyncBaseTransport):
    def __init__(self, port: int):
        self.port = port
        self.inner = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        local = httpx.Request(
            request.method,
            f"http://127.0.0.1:{self.port}/fixture",
            headers=request.headers,
            content=request.content,
            extensions=request.extensions,
        )
        return await self.inner.handle_async_request(local)

    async def aclose(self) -> None:
        await self.inner.aclose()


@pytest.mark.parametrize("overall", [None, 1.0])
async def test_http200_reasoning_then_real_httpx_read_idle_keeps_observed_phase(
    overall: float | None,
) -> None:
    finished = asyncio.Event()

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            length = next(
                int(line.split(b":")[1])
                for line in headers.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            await reader.readexactly(length)
            writer.write(
                b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n"
                b"Content-Type: text/event-stream\r\n\r\n"
            )
            chunk = b'data: {"type":"response.reasoning_summary_text.delta","delta":"working"}\n\n'
            writer.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
            await writer.drain()
            await asyncio.sleep(0.12)
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()
            finished.set()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = int(server.sockets[0].getsockname()[1])
    try:
        executor = CodexHttpExecutor(
            Session(),
            transport=LocalTransport(port),
            timeout_policy=TransportTimeouts(
                read_idle_seconds=Decimal("0.04"),
                connect_seconds=Decimal("0.2"),
                write_seconds=Decimal("0.2"),
                pool_seconds=Decimal("0.2"),
                policy_ref="controlled-idle-fixture",
            ),
        )
        prepared = executor.prepare(
            "fixture", {"type": "object"}, output_tokens=100, timeout_seconds=overall
        )
        with pytest.raises(
            ModelTransportHold, match="OAUTH_READ_IDLE_TIMEOUT_REMOTE_STOP_UNKNOWN"
        ) as caught:
            await executor.dispatch(prepared)
        observation = caught.value.observation
        assert observation.http_status == 200 and observation.received_bytes > 0
        assert observation.last_event_type == "response.reasoning_summary_text.delta"
        assert (
            observation.timeout_kind == "READ_IDLE"
            and observation.last_transport_phase == "READ_BODY"
        )
        assert observation.transport_closed is True
        assert prepared.transport_timeouts == executor.timeout_policy
        assert (
            observation.transport_timeouts
            and observation.transport_timeouts.read_idle_seconds == Decimal("0.04")
        )
        await asyncio.wait_for(finished.wait(), 1)
    finally:
        server.close()
        await server.wait_closed()


async def test_continuous_events_outlive_idle_window_without_total_cutoff() -> None:
    # Scale one logical minute to0.1s: twenty minutes total, events every four,
    # five-minute read-idle. Real HTTPX socket timeout, not a mock timeout oracle.
    finished = asyncio.Event()

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            length = next(
                int(line.split(b":")[1])
                for line in headers.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            await reader.readexactly(length)
            writer.write(
                b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n"
                b"Content-Type: text/event-stream\r\n\r\n"
            )
            await writer.drain()
            for _ in range(5):
                await asyncio.sleep(0.4)
                event = b'data: {"type":"response.in_progress"}\n\n'
                writer.write(f"{len(event):x}\r\n".encode() + event + b"\r\n")
                await writer.drain()
            event = (
                b"data: "
                + json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "fixture",
                            "output": [
                                {
                                    "type": "message",
                                    "content": [{"type": "output_text", "text": '{"ok":true}'}],
                                }
                            ],
                        },
                    }
                ).encode()
                + b"\n\n"
            )
            writer.write(f"{len(event):x}\r\n".encode() + event + b"\r\n0\r\n\r\n")
            await writer.drain()
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()
            finished.set()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    try:
        executor = CodexHttpExecutor(
            Session(),
            transport=LocalTransport(server.sockets[0].getsockname()[1]),
            timeout_policy=TransportTimeouts(
                connect_seconds=Decimal("1"),
                read_idle_seconds=Decimal("0.5"),
                write_seconds=Decimal("1"),
                pool_seconds=Decimal("1"),
                policy_ref="scaled-liveness-fixture",
            ),
        )
        prepared = executor.prepare(
            "fixture", {"type": "object"}, output_tokens=100, timeout_seconds=None
        )
        started = monotonic()
        result = await executor.dispatch(prepared)
        assert monotonic() - started >= 2.0 and result.text == '{"ok":true}'
        assert result.observation and result.observation.timeout_ms is None
        assert result.observation.frame_counts["response.in_progress"] == 5
        assert result.observation.transport_closed is True
        await asyncio.wait_for(finished.wait(), 1)
    finally:
        server.close()
        await server.wait_closed()
