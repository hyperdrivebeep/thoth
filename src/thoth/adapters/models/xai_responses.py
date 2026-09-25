"""Direct xAI Responses transport. Codex account headers and retry policy are not used."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from dataclasses import replace
from decimal import Decimal

import httpx
from openai import DEFAULT_TIMEOUT

from thoth.adapters.models.http_rejection import read_rejection_metadata
from thoth.adapters.models.receive_stats import ReceiveStats
from thoth.adapters.models.sse_stream import parse_responses_sse
from thoth.adapters.models.xai_oauth import OmoXaiSessionReader, XaiSession
from thoth.domain.model_dispatch import (
    ModelControlCapability,
    ModelTransportReply,
    PreparedModelDispatch,
    TransportTimeouts,
)
from thoth.domain.model_settings import ResolvedModelSettings
from thoth.ports.model import ModelExecutionHold, ModelTransportCancelled, ModelTransportHold

XAI_CONTROL = ModelControlCapability(
    capability_id="xai-responses-http-local-bounds",
    output_control="OBSERVATION_ONLY",
    version="1.0.0",
    native_tools="NONE",
    cancellation="LOCAL_TRANSPORT",
    owns_serialization=True,
)
_XAI_ENDPOINT = "https://api.x.ai/v1/responses"


class XaiResponsesExecutor:
    control_capability = XAI_CONTROL

    def __init__(
        self,
        session: OmoXaiSessionReader,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_policy: TransportTimeouts | None = None,
    ) -> None:
        self.session = session
        self.transport = transport
        self.timeout_policy = timeout_policy
        self._model_label = "xai/current-settings"

    @property
    def model_label(self) -> str:
        return self._model_label

    def prepare(
        self,
        prompt: str,
        schema: dict[str, object],
        *,
        output_tokens: int,
        timeout_seconds: float | None,
        model_settings: ResolvedModelSettings | None = None,
    ) -> PreparedModelDispatch:
        settings = self.session.read()
        if model_settings is not None:
            if model_settings.provider != "xai" or model_settings.model is None:
                raise ModelExecutionHold("XAI_MODEL_SETTINGS_MISMATCH")
            settings = XaiSession(
                settings.access_token,
                model_settings.model,
                model_settings.reasoning_effort,
            )
        self._model_label = f"xai/{settings.model}"
        body: dict[str, object] = {
            "model": settings.model,
            "store": False,
            "stream": True,
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "thoth_result",
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        if settings.reasoning_effort:
            body["reasoning"] = {"effort": settings.reasoning_effort}
        policy = self.timeout_policy
        if timeout_seconds is None:
            policy = TransportTimeouts(
                connect_seconds=Decimal(str(DEFAULT_TIMEOUT.connect)),
                read_idle_seconds=Decimal(str(DEFAULT_TIMEOUT.read)),
                write_seconds=Decimal(str(DEFAULT_TIMEOUT.write)),
                pool_seconds=Decimal(str(DEFAULT_TIMEOUT.pool)),
                policy_ref="OPENAI_SDK_IO_DEFAULTS",
            ).model_copy(update={} if policy is None else policy.model_dump(exclude_none=True))
        return PreparedModelDispatch(
            json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(),
            output_tokens,
            None,
            None,
            timeout_seconds,
            self.control_capability,
            None if policy is None else policy.bounded(timeout_seconds),
        )

    async def dispatch(self, request: PreparedModelDispatch) -> ModelTransportReply:
        settings = self.session.read()
        stats = ReceiveStats()
        timeouts = (request.transport_timeouts or TransportTimeouts()).bounded(
            request.timeout_seconds
        )
        client = httpx.AsyncClient(
            transport=self.transport,
            follow_redirects=False,
            timeout=httpx.Timeout(
                connect=None
                if timeouts.connect_seconds is None
                else float(timeouts.connect_seconds),
                read=None
                if timeouts.read_idle_seconds is None
                else float(timeouts.read_idle_seconds),
                write=None if timeouts.write_seconds is None else float(timeouts.write_seconds),
                pool=None if timeouts.pool_seconds is None else float(timeouts.pool_seconds),
            ),
        )
        reply: ModelTransportReply | None = None
        cause: BaseException | None = None
        reason = ""
        try:
            reply = await asyncio.wait_for(
                self._receive(client, request, settings, stats), request.timeout_seconds
            )
        except asyncio.CancelledError as exc:
            stats.local_cancel_requested = True
            cause = exc
        except TimeoutError as exc:
            stats.timeout_kind = "OVERALL"
            cause, reason = exc, "XAI_TRANSPORT_DEADLINE_REMOTE_STOP_UNKNOWN"
        except httpx.TimeoutException as exc:
            stats.timeout_kind = (
                "CONNECT"
                if isinstance(exc, httpx.ConnectTimeout)
                else "READ_IDLE"
                if isinstance(exc, httpx.ReadTimeout)
                else "WRITE"
                if isinstance(exc, httpx.WriteTimeout)
                else "POOL"
                if isinstance(exc, httpx.PoolTimeout)
                else "UNKNOWN"
            )
            cause, reason = exc, f"XAI_{stats.timeout_kind}_TIMEOUT_REMOTE_STOP_UNKNOWN"
            stats.capture_httpx_exception(exc)
        except httpx.HTTPError as exc:
            cause, reason = exc, "XAI_TRANSPORT_FAILURE"
            stats.capture_httpx_exception(exc)
        except ModelExecutionHold as exc:
            cause, reason = exc, str(exc)
        finally:
            stats.freeze_terminal()
            stats.transport_closed = False
            with suppress(TimeoutError, httpx.HTTPError):
                await asyncio.wait_for(client.aclose(), 2)
                stats.transport_closed = True
        if isinstance(cause, asyncio.CancelledError):
            raise ModelTransportCancelled(stats.snapshot(request)) from cause
        if cause is not None:
            raise ModelTransportHold(reason, stats.snapshot(request)) from cause
        assert reply is not None
        return replace(reply, observation=stats.snapshot(request))

    async def _receive(
        self,
        client: httpx.AsyncClient,
        request: PreparedModelDispatch,
        settings: XaiSession,
        stats: ReceiveStats,
    ) -> ModelTransportReply:
        async def trace(event: str, info: dict[str, object]) -> None:
            del info
            if event.startswith(("connection.connect_tcp.", "connection.start_tls.")):
                stats.last_transport_phase = "CONNECT"
            elif ".send_request_" in event:
                stats.last_transport_phase = "WRITE"
            elif ".receive_response_headers." in event:
                stats.last_transport_phase = "READ_HEADERS"
            elif ".receive_response_body." in event:
                stats.last_transport_phase = "READ_BODY"

        async with client.stream(
            "POST",
            _XAI_ENDPOINT,
            headers={
                "Authorization": "Bearer " + settings.access_token,
                "Content-Type": "application/json",
            },
            content=request.payload,
            extensions={"trace": trace},
        ) as response:
            stats.http_status = response.status_code
            stats.capture_response(response)
            stats.first_response_ms = stats.elapsed_ms()
            stats.last_transport_phase = "READ_HEADERS"
            if response.status_code != 200:
                reason = (
                    "AUTH_REQUIRED" if response.status_code in (401, 403) else "REQUEST_REJECTED"
                )
                try:
                    stats.http_rejection = await read_rejection_metadata(response)
                except Exception:
                    stats.http_rejection = None
                raise ModelExecutionHold(f"XAI_{reason}_{response.status_code}")
            return await parse_responses_sse(response, request, stats, reason_prefix="XAI")
