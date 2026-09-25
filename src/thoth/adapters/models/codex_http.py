"""Existing Codex OAuth, with an explicitly approved local output/transport boundary.

No tools, account migration, model fallback, login or credentials are exposed to model input.
The upstream does not support a per-request generated/billed token ceiling.
"""

import asyncio
import json
import os
import tomllib
from contextlib import suppress
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import httpx
from openai import DEFAULT_TIMEOUT

from thoth.adapters.models.http_rejection import read_rejection_metadata
from thoth.adapters.models.receive_stats import ReceiveStats
from thoth.adapters.models.sse_stream import parse_responses_sse
from thoth.domain.model_dispatch import (
    ModelControlCapability,
    ModelTransportReply,
    OAuthSession,
    PreparedModelDispatch,
    TransportTimeouts,
)
from thoth.domain.model_settings import ResolvedModelSettings
from thoth.ports.model import ModelExecutionHold, ModelTransportCancelled, ModelTransportHold
from thoth.ports.model_transport import OAuthSessionPort

OAUTH_LOCAL_CONTROL = ModelControlCapability(
    capability_id="codex-oauth-http-local-bounds",
    output_control="OBSERVATION_ONLY",
    version="2.0.0",
    native_tools="NONE",
    cancellation="LOCAL_TRANSPORT",
    owns_serialization=True,
)


class CodexLocalSession:
    def __init__(self, root: Path | None = None, model: str | None = None) -> None:
        self.root = root or Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        self.model = model

    def read(self) -> OAuthSession:
        try:
            auth = json.loads((self.root / "auth.json").read_text(encoding="utf-8-sig"))
            config = tomllib.loads((self.root / "config.toml").read_text(encoding="utf-8-sig"))
            tokens = auth["tokens"]
            model = self.model or config["model"]
            if not all(
                isinstance(v, str) and v
                for v in (tokens["access_token"], tokens["account_id"], model)
            ):
                raise ValueError("missing settings")
            return OAuthSession(
                tokens["access_token"],
                tokens["account_id"],
                model,
                config.get("model_reasoning_effort"),
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ModelExecutionHold("OAUTH_CURRENT_SESSION_UNAVAILABLE") from exc


class CodexHttpExecutor:
    control_capability = OAUTH_LOCAL_CONTROL

    def __init__(
        self,
        session: OAuthSessionPort,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_policy: TransportTimeouts | None = None,
    ) -> None:
        self.session = session
        self.transport = transport
        self.timeout_policy = timeout_policy
        self._model_label = "codex-oauth/current-settings"

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
            if model_settings.provider != "codex-oauth" or model_settings.model is None:
                raise ModelExecutionHold("OAUTH_MODEL_SETTINGS_MISMATCH")
            settings = OAuthSession(
                settings.access_token,
                settings.account_id,
                model_settings.model,
                model_settings.reasoning_effort,
            )
        self._model_label = f"codex-oauth/{settings.model}"
        body: dict[str, object] = {
            "model": settings.model,
            "store": False,
            "stream": True,
            "instructions": "Return the requested schema object. No tools are available. "
            "Keep explanations concise while retaining necessary evidence and uncertainty.",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                }
            ],
            "tools": [],
            "tool_choice": "none",
            "parallel_tool_calls": False,
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
            # No overall research clock. Retain the installed SDK's I/O liveness defaults;
            # read-idle resets on traffic and is not a total response-duration limit.
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
        # The immutable prepared request owns model/effort; read only current credentials here.
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
            cause, reason = exc, "OAUTH_TRANSPORT_DEADLINE_REMOTE_STOP_UNKNOWN"
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
            cause, reason = exc, f"OAUTH_{stats.timeout_kind}_TIMEOUT_REMOTE_STOP_UNKNOWN"
            stats.capture_httpx_exception(exc)
        except httpx.HTTPError as exc:
            cause, reason = exc, "OAUTH_TRANSPORT_FAILURE"
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
        settings: OAuthSession,
        stats: ReceiveStats,
    ) -> ModelTransportReply:
        async def trace(event: str, info: dict[str, object]) -> None:
            # Only event identity is observed. Trace data may contain credentials/body.
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
            "https://chatgpt.com/backend-api/codex/responses",
            headers={
                "Authorization": "Bearer " + settings.access_token,
                "ChatGPT-Account-Id": settings.account_id,
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
                if (
                    stats.http_rejection is not None
                    and stats.http_rejection.classification_basis == "code:model_not_supported"
                ):
                    reason = "MODEL_NOT_SUPPORTED"
                raise ModelExecutionHold(f"OAUTH_{reason}_{response.status_code}")
            return await parse_responses_sse(response, request, stats, reason_prefix="OAUTH")
