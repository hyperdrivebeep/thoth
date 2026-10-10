"""Correlation IDs and error excerpts are useful only after bounded privacy filtering."""

import json
from collections.abc import AsyncIterator

import httpx
import pytest

from thoth.adapters.models.codex_http import CodexHttpExecutor
from thoth.adapters.models.transport_diagnostic import exception_detail, response_detail
from thoth.adapters.models.transport_message import safe_exception_prefix
from thoth.domain.model_dispatch import OAuthSession, TransportDiagnostic
from thoth.ports.model import ModelTransportHold

MESSAGE = "peer closed connection without sending complete message body (incomplete chunked read)"
REQUEST_ID = "b5c60fb9-2ee6-4ca4-b44d-30b7234660ad"
RAY_ID = "abcdef0123456789-ICN"


@pytest.mark.parametrize(
    "suffix",
    [
        " Authorization: Bearer private-token",
        " Proxy-Authorization: Basic cHJpdmF0ZQ==",
        " Cookie: session=private-cookie; other=private-other",
        " Set-Cookie: session=private-cookie\r\nprivate-next-line",
        ' {"access_token":"private-token", "refresh_token":"private-refresh"}',
        " access-token=private-token",
        " api_key: private-key",
        " password=private-password",
        " https://private-user:private-password@example.invalid/?key=private-query",
        " PRIVATE_BARE_TOKEN_12345",
        " sk-proj-privateapikey123456789",
        " eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJwcml2YXRlIn0.privateSignature",
        " connection",  # A known credential can be an otherwise allowed word.
    ],
)
def test_error_prefix_masks_secret_context_and_unlabelled_credentials(suffix: str) -> None:
    text = safe_exception_prefix(httpx.RemoteProtocolError(MESSAGE + suffix), ("connection",))
    assert text is not None and text.startswith("peer closed [REDACTED]")
    for secret in ("private", "PRIVATE", "cHJpdmF0ZQ", "eyJ", "connection"):
        assert secret not in text
    assert "incomplete chunked read" in text
    assert len(text) <= 256 and text.isascii() and "\n" not in text and "\r" not in text


def test_normal_h11_message_is_retained_but_excerpts_are_bounded() -> None:
    assert safe_exception_prefix(httpx.RemoteProtocolError(MESSAGE), ()) == MESSAGE
    assert len(safe_exception_prefix(httpx.ReadError("peer " * 1000), ()) or "") == 256
    # Redact before output truncation, including a credential across that boundary.
    text = safe_exception_prefix(httpx.ReadError("peer " * 48 + "a-secret-crossing-boundary"), ())
    assert text is not None and "secret" not in text and "boundary" not in text


def test_message_formatting_failure_does_not_destroy_exception_class() -> None:
    class Unprintable(httpx.ReadError):
        def __str__(self) -> str:
            raise ValueError("unavailable")

    error = Unprintable("unused")
    assert safe_exception_prefix(error, ()) is None
    assert exception_detail(error) == {"httpx_error_type": "UNKNOWN"}


@pytest.mark.parametrize(
    "request_id,ray_id",
    [
        ("bad value", "invalid"),
        ("a" * 129, "a" * 21),
        ("bad\r\nAuthorization: secret", "abcdef0123456789-ICN\n"),
        ("Bearer-secret", "abcdef0123456789-ICN-secret"),
        ("sk-secret", "abcdef0123456789-icn"),
    ],
)
def test_invalid_header_values_are_omitted(request_id: str, ray_id: str) -> None:
    result = response_detail(
        httpx.Response(200, headers={"x-oai-request-id": request_id, "cf-ray": ray_id})
    )
    assert "x_oai_request_id" not in result and "cf_ray" not in result


def test_only_explicit_valid_headers_are_kept_and_known_secrets_are_rejected() -> None:
    response = httpx.Response(
        200,
        headers={
            "X-OAI-Request-ID": REQUEST_ID,
            "CF-Ray": RAY_ID,
            "Authorization": "private-token",
            "Set-Cookie": "private-cookie",
            "Server": "private-server",
        },
    )
    result = response_detail(response)
    assert result["x_oai_request_id"] == REQUEST_ID and result["cf_ray"] == RAY_ID
    assert "private" not in json.dumps(result)
    result = response_detail(response, (REQUEST_ID, RAY_ID))
    assert "x_oai_request_id" not in result and "cf_ray" not in result
    result = response_detail(httpx.Response(200))
    assert "x_oai_request_id" not in result and "cf_ray" not in result


def test_optional_diagnostic_fields_decode_legacy_records_and_enforce_bounds() -> None:
    legacy = TransportDiagnostic.model_validate({"httpx_error_type": "RemoteProtocolError"})
    assert (
        legacy.x_oai_request_id is None
        and legacy.cf_ray is None
        and legacy.error_message_prefix is None
    )
    for field, value in (
        ("error_message_prefix", "x" * 257),
        ("x_oai_request_id", "x" * 129),
        ("cf_ray", "bad"),
    ):
        with pytest.raises(ValueError):
            TransportDiagnostic.model_validate({field: value})


@pytest.mark.parametrize("fail", [True, False])
async def test_codex_created_then_disconnect_records_ids_without_retry_or_token_leak(
    fail: bool,
) -> None:
    class Session:
        def read(self) -> OAuthSession:
            return OAuthSession("connection", "peer", "diagnostic-fixture", "high")

    class Stream(httpx.AsyncByteStream):
        closed = 0

        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b'data: {"type":"response.created","response":{"id":"response-fixture"}}\n\n'
            if fail:
                raise httpx.RemoteProtocolError(MESSAGE + " Cookie: private-cookie")
            yield (
                b'data: {"type":"response.completed",'
                b'"response":{"id":"response-fixture","output":[]}}\n\n'
            )

        async def aclose(self) -> None:
            self.closed += 1

    stream = Stream()
    calls: list[httpx.Request] = []

    def serve(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200, stream=stream, headers={"x-oai-request-id": REQUEST_ID, "cf-ray": RAY_ID}
        )

    executor = CodexHttpExecutor(Session(), transport=httpx.MockTransport(serve))
    prepared = executor.prepare(
        "fixture", {"type": "object"}, output_tokens=6000, timeout_seconds=None
    )
    if fail:
        with pytest.raises(ModelTransportHold, match="OAUTH_TRANSPORT_FAILURE") as caught:
            await executor.dispatch(prepared)
        observation = caught.value.observation
    else:
        observation = (await executor.dispatch(prepared)).observation
    assert observation is not None and observation.transport_diagnostic is not None
    diagnostic = observation.transport_diagnostic
    assert diagnostic.x_oai_request_id == REQUEST_ID and diagnostic.cf_ray == RAY_ID
    assert observation.visible_output_bytes == 0 and observation.transport_closed
    assert len(calls) == 1 and stream.closed == 1
    if fail:
        assert diagnostic.httpx_error_type == "RemoteProtocolError"
        assert diagnostic.error_message_prefix is not None
        assert "incomplete chunked read" in diagnostic.error_message_prefix
        assert (
            "peer" not in diagnostic.error_message_prefix
            and "connection" not in diagnostic.error_message_prefix
        )
        assert "private-cookie" not in observation.model_dump_json()
    else:
        assert diagnostic.error_message_prefix is None and diagnostic.httpx_error_type is None
