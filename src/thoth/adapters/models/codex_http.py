"""Existing Codex OAuth, with an explicitly approved local output/transport boundary.

No tools, account migration, model fallback, login or credentials are exposed to model input.
The upstream does not support a per-request generated/billed token ceiling.
"""

import asyncio
import json
import time
import weakref
from contextlib import suppress
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import cast

import httpx
from openai import DEFAULT_TIMEOUT

from thoth.adapters.models.codex_broker import CodexAuthBroker, broker_for_workspace
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
    def __init__(
        self,
        workspace: Path,
        model: str | None = None,
        *,
        broker: CodexAuthBroker | None = None,
    ) -> None:
        self.broker = broker or broker_for_workspace(workspace)
        self.model = model

    def read(self) -> OAuthSession:
        return self.broker.session(self.model)

    def read_bound(self) -> tuple[OAuthSession, str]:
        return self.broker.session_bound(self.model)

    def validate_selection(self, model: str, effort: str | None) -> None:
        self.broker.validate_selection(model, effort)


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
        self._prepared_accounts: dict[
            int, tuple[weakref.ReferenceType[PreparedModelDispatch], str, str | None]
        ] = {}

    def _bind(
        self, prepared: PreparedModelDispatch, account_id: str, auth_digest: str | None
    ) -> None:
        key = id(prepared)
        owner = weakref.ref(self)

        def release(_ref: weakref.ReferenceType[PreparedModelDispatch]) -> None:
            executor = owner()
            if executor is not None:
                executor._prepared_accounts.pop(key, None)

        self._prepared_accounts[key] = (weakref.ref(prepared, release), account_id, auth_digest)

    def with_observation_limits(
        self,
        prepared: PreparedModelDispatch,
        *,
        max_visible_output_bytes: int | None = None,
        max_frame_bytes: int | None = None,
    ) -> PreparedModelDispatch:
        """Keep the same account/payload when a caller adds local receive limits."""
        bound = self._prepared_accounts.get(id(prepared))
        if bound is None or bound[0]() is not prepared:
            raise ModelExecutionHold("OAUTH_PREPARED_BINDING_MISSING")
        updates: dict[str, object] = {}
        if max_visible_output_bytes is not None:
            updates["max_visible_output_bytes"] = max_visible_output_bytes
        if max_frame_bytes is not None:
            updates["max_frame_bytes"] = max_frame_bytes
        copy = replace(prepared, **updates)
        self._bind(copy, bound[1], bound[2])
        return copy

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
        if isinstance(self.session, CodexLocalSession):
            settings, auth_digest = self.session.read_bound()
        else:
            settings, auth_digest = self.session.read(), None
        if model_settings is not None:
            if (
                model_settings.provider != "codex-oauth"
                or model_settings.model is None
                or (
                    isinstance(self.session, CodexLocalSession)
                    and model_settings.model != settings.model
                )
            ):
                raise ModelExecutionHold("OAUTH_MODEL_SETTINGS_MISMATCH")
            if isinstance(self.session, CodexLocalSession):
                self.session.validate_selection(
                    model_settings.model, model_settings.reasoning_effort
                )
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
        prepared = PreparedModelDispatch(
            json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(),
            output_tokens,
            None,
            None,
            timeout_seconds,
            self.control_capability,
            None if policy is None else policy.bounded(timeout_seconds),
        )
        self._bind(prepared, settings.account_id, auth_digest)
        return prepared

    async def dispatch(self, request: PreparedModelDispatch) -> ModelTransportReply:
        started = time.monotonic()
        auth_limit = 15 if request.timeout_seconds is None else min(15, request.timeout_seconds)
        try:
            if isinstance(self.session, CodexLocalSession):
                settings, auth_digest = await asyncio.wait_for(
                    asyncio.to_thread(self.session.read_bound), timeout=auth_limit
                )
            else:
                settings = await asyncio.wait_for(
                    asyncio.to_thread(self.session.read), timeout=auth_limit
                )
                auth_digest = None
        except TimeoutError as exc:
            raise ModelExecutionHold("OAUTH_AUTH_DEADLINE") from exc
        bound = self._prepared_accounts.get(id(request))
        if bound is None or bound[0]() is not request or bound[1] != settings.account_id:
            raise ModelExecutionHold("OAUTH_ACCOUNT_CHANGED_BEFORE_DISPATCH")
        if bound[2] != auth_digest:
            raise ModelExecutionHold("OAUTH_AUTH_SNAPSHOT_CHANGED_BEFORE_DISPATCH")
        try:
            payload_value: object = json.loads(request.payload)
            if not isinstance(payload_value, dict):
                raise TypeError("prepared payload must be an object")
            payload = cast(dict[str, object], payload_value)
            model = payload["model"]
            reasoning = payload.get("reasoning")
            effort = (
                cast(dict[str, object], reasoning).get("effort")
                if isinstance(reasoning, dict)
                else None
            )
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ModelExecutionHold("OAUTH_PREPARED_PAYLOAD_INVALID") from exc
        if isinstance(self.session, CodexLocalSession) and model != settings.model:
            raise ModelExecutionHold("OAUTH_MODEL_CHANGED_BEFORE_DISPATCH")
        if isinstance(self.session, CodexLocalSession):
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(
                        self.session.validate_selection,
                        settings.model,
                        effort if isinstance(effort, str) else None,
                    ),
                    timeout=auth_limit,
                )
            except TimeoutError as exc:
                raise ModelExecutionHold("OAUTH_AUTH_DEADLINE") from exc
        remaining = (
            None
            if request.timeout_seconds is None
            else request.timeout_seconds - (time.monotonic() - started)
        )
        if remaining is not None and remaining <= 0:
            raise ModelExecutionHold("OAUTH_TRANSPORT_DEADLINE_REMOTE_STOP_UNKNOWN")
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
                self._receive(client, request, settings, stats, expected_digest=auth_digest),
                remaining,
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
        *,
        expected_digest: str | None,
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

        outgoing = client.build_request(
            "POST",
            "https://chatgpt.com/backend-api/codex/responses",
            headers={
                "Authorization": "Bearer " + settings.access_token,
                "ChatGPT-Account-Id": settings.account_id,
                "Content-Type": "application/json",
            },
            content=request.payload,
            extensions={"trace": trace},
        )
        if isinstance(self.session, CodexLocalSession):
            async with self.session.broker.dispatch_gate_async(
                settings, expected_digest=expected_digest
            ):
                response = await client.send(outgoing, stream=True)
        else:
            response = await client.send(outgoing, stream=True)
        try:
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
        finally:
            await response.aclose()
