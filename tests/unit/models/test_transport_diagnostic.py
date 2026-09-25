import asyncio
import json
import ssl
from collections.abc import AsyncIterator

import httpx
import pytest

import thoth.adapters.models.receive_stats as receive_stats
from thoth.adapters.models.codex_http import CodexHttpExecutor
from thoth.adapters.models.receive_stats import ReceiveStats
from thoth.adapters.models.transport_diagnostic import exception_detail, response_detail
from thoth.domain.model_dispatch import ModelReceiveObservation, OAuthSession, TransportDiagnostic
from thoth.ports.model import ModelTransportCancelled, ModelTransportHold

SECRETS = (
    "AUTH_CANARY",
    "ACCOUNT_CANARY",
    "COOKIE_CANARY",
    "QUERY_CANARY",
    "MESSAGE_CANARY",
    "VISIBLE_CANARY",
    "REASONING_CANARY",
    "SERVER_CANARY",
)


class DiagnosticSession:
    def read(self) -> OAuthSession:
        return OAuthSession(SECRETS[0], SECRETS[1], "diagnostic-fixture", "high")


class FailingStream(httpx.AsyncByteStream):
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.started = asyncio.Event()
        self.close_count = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for event in (
            {"type": "response.created", "response": {"id": "diagnostic-response"}},
            {"type": "response.reasoning_summary_text.delta", "delta": SECRETS[6]},
            {"type": "response.output_text.delta", "delta": SECRETS[5]},
        ):
            yield b"data: " + json.dumps(event).encode() + b"\n\n"
        self.started.set()
        if self.error is not None:
            raise self.error
        await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.close_count += 1


def failure_response(stream: FailingStream) -> httpx.Response:
    return httpx.Response(
        200,
        stream=stream,
        headers={
            "Content-Encoding": "identity",
            "Transfer-Encoding": "chunked",
            "Authorization": SECRETS[0],
            "Set-Cookie": SECRETS[2],
            "Server": SECRETS[7],
        },
        extensions={"http_version": b"HTTP/1.1"},
    )


def assert_private(value: str) -> None:
    for secret in SECRETS:
        assert secret not in value


@pytest.mark.parametrize(
    "kind", [httpx.ReadError, httpx.RemoteProtocolError, httpx.DecodingError, httpx.ReadTimeout]
)
async def test_httpx_subtypes_and_safe_metadata_survive_transport_hold(kind: type[httpx.HTTPError]):
    error = kind(f"{SECRETS[4]} https://example.invalid/?key={SECRETS[3]}")
    error.__cause__ = OSError(104, SECRETS[4])
    stream = FailingStream(error)
    executor = CodexHttpExecutor(
        DiagnosticSession(), transport=httpx.MockTransport(lambda _: failure_response(stream))
    )
    prepared = executor.prepare(
        "fixture", {"type": "object"}, output_tokens=6000, timeout_seconds=None
    )
    with pytest.raises(ModelTransportHold) as caught:
        await executor.dispatch(prepared)
    expected = (
        "OAUTH_READ_IDLE_TIMEOUT_REMOTE_STOP_UNKNOWN"
        if kind is httpx.ReadTimeout
        else "OAUTH_TRANSPORT_FAILURE"
    )
    assert str(caught.value) == expected and caught.value.__cause__ is error
    observation = caught.value.observation
    diagnostic = observation.transport_diagnostic
    assert diagnostic is not None and diagnostic.capture_state == "CAPTURED"
    assert diagnostic.httpx_error_type == kind.__name__
    assert diagnostic.nested_cause_category == "OS_ERROR" and diagnostic.nested_errno == 104
    assert diagnostic.cause_chain_state == "COMPLETE"
    assert diagnostic.http_version == "HTTP/1.1" and diagnostic.content_encoding == "IDENTITY"
    assert diagnostic.body_framing == "CHUNKED" and diagnostic.last_receive_gap_ms is not None
    assert observation.last_transport_phase == "READ_BODY" and observation.http_status == 200
    assert observation.transport_closed and stream.close_count == 1
    assert_private(observation.model_dump_json())


def test_nested_cause_bounds_cycles_ssl_and_no_message_access():
    error = httpx.ReadError("unused")
    error.__cause__ = ssl.SSLError(1, "unused")
    assert exception_detail(error)["nested_cause_category"] == "SSL_ERROR"
    error.__cause__ = error
    assert exception_detail(error)["cause_chain_state"] == "CYCLE"
    tail: BaseException = OSError(104, "unused")
    for _ in range(5):
        head = RuntimeError("unused")
        head.__cause__ = tail
        tail = head
    error.__cause__ = tail
    detail = exception_detail(error)
    assert detail["cause_chain_state"] == "DEPTH_LIMIT" and detail["nested_errno"] is None

    class CannotStringify(Exception):
        def __str__(self) -> str:
            raise AssertionError("Must not stringify a cause")

    error.__cause__ = CannotStringify()
    assert exception_detail(error)["nested_cause_category"] == "UNKNOWN"
    oversized = OSError(2**40, "unused")
    error.__cause__ = oversized
    assert exception_detail(error)["nested_errno"] is None
    assert exception_detail(httpx.ReadError("unused")) == {"httpx_error_type": "ReadError"}


@pytest.mark.parametrize(
    "encoding,expected",
    [
        (None, "ABSENT"),
        ("identity", "IDENTITY"),
        ("gzip", "GZIP"),
        ("deflate", "DEFLATE"),
        ("br", "BR"),
        ("zstd", "ZSTD"),
        ("gzip, br", "MULTIPLE"),
        ("SECRET_CANARY", "OTHER"),
    ],
)
def test_header_values_are_categories_only(encoding: str | None, expected: str):
    headers = {"Transfer-Encoding": "chunked", "Server": "SERVER_CANARY"}
    if encoding is not None:
        headers["Content-Encoding"] = encoding
    fields = response_detail(
        httpx.Response(200, headers=headers, extensions={"http_version": b"HTTP/1.1"})
    )
    assert fields["content_encoding"] == expected and fields["body_framing"] == "CHUNKED"
    assert "CANARY" not in json.dumps(fields)


def test_framing_and_unknown_version():
    assert response_detail(httpx.Response(200))["body_framing"] == "ABSENT"
    assert (
        response_detail(httpx.Response(200, headers={"Content-Length": "123"}))["body_framing"]
        == "CONTENT_LENGTH"
    )
    assert (
        response_detail(
            httpx.Response(200, headers={"Content-Length": "123", "Transfer-Encoding": "chunked"})
        )["body_framing"]
        == "CONFLICT"
    )
    assert (
        response_detail(httpx.Response(200, headers={"Transfer-Encoding": "secret"}))[
            "body_framing"
        ]
        == "OTHER"
    )
    assert (
        response_detail(httpx.Response(200, extensions={"http_version": b"secret"}))["http_version"]
        == "UNKNOWN"
    )


async def test_collection_failure_does_not_replace_primary(monkeypatch: pytest.MonkeyPatch):
    def broken(error: httpx.HTTPError) -> dict[str, object]:
        raise ValueError("diagnostic helper fault")

    monkeypatch.setattr(receive_stats, "exception_detail", broken)
    original = httpx.ReadError("original")
    executor = CodexHttpExecutor(
        DiagnosticSession(),
        transport=httpx.MockTransport(lambda _: failure_response(FailingStream(original))),
    )
    with pytest.raises(ModelTransportHold, match="OAUTH_TRANSPORT_FAILURE") as caught:
        await executor.dispatch(
            executor.prepare(
                "fixture", {"type": "object"}, output_tokens=6000, timeout_seconds=None
            )
        )
    assert caught.value.__cause__ is original
    diagnostic = caught.value.observation.transport_diagnostic
    assert diagnostic is not None
    assert diagnostic == TransportDiagnostic(
        capture_state="UNAVAILABLE",
        http_version="HTTP/1.1",
        content_encoding="IDENTITY",
        body_framing="CHUNKED",
        last_receive_gap_ms=diagnostic.last_receive_gap_ms,
    )


async def test_success_gap_frozen_before_cleanup(monkeypatch: pytest.MonkeyPatch):
    clock = [1.0]
    monkeypatch.setattr(receive_stats, "monotonic", lambda: clock[0])

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            clock[0] = 2.0
            yield (
                b'data: {"type":"response.completed","response":{"id":"complete","output":[]}}\n\n'
            )

        async def aclose(self) -> None:
            clock[0] = 20.0

    executor = CodexHttpExecutor(
        DiagnosticSession(),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Body())),
    )
    reply = await executor.dispatch(
        executor.prepare("fixture", {"type": "object"}, output_tokens=6000, timeout_seconds=None)
    )
    assert reply.observation and reply.observation.transport_diagnostic
    assert reply.observation.transport_diagnostic.last_receive_gap_ms == 0
    assert reply.observation.transport_diagnostic.httpx_error_type is None
    assert reply.observation.transport_closed and reply.observation.elapsed_ms == 19000


@pytest.mark.parametrize("cancel", [False, True])
async def test_outer_deadline_and_cancel_do_not_invent_httpx_type(cancel: bool):
    stream = FailingStream()
    executor = CodexHttpExecutor(
        DiagnosticSession(), transport=httpx.MockTransport(lambda _: failure_response(stream))
    )
    prepared = executor.prepare(
        "fixture", {"type": "object"}, output_tokens=6000, timeout_seconds=None if cancel else 0.03
    )
    task = asyncio.create_task(executor.dispatch(prepared))
    await stream.started.wait()
    if cancel:
        task.cancel()
    with pytest.raises(ModelTransportCancelled if cancel else ModelTransportHold) as caught:
        await task
    observation = caught.value.observation
    assert (
        observation.transport_diagnostic
        and observation.transport_diagnostic.httpx_error_type is None
    )
    assert observation.timeout_kind == (None if cancel else "OVERALL")
    assert observation.local_cancel_requested is cancel and stream.close_count == 1


def test_old_observation_decodes_without_new_diagnostic():
    record = ModelReceiveObservation.model_validate(
        {
            "received_bytes": 1,
            "visible_output_bytes": 0,
            "frame_counts": {},
            "max_stream_bytes": 100,
            "max_visible_output_bytes": 50,
            "timeout_ms": 1000,
        }
    )
    assert record.transport_diagnostic is None
    with pytest.raises(ValueError):
        TransportDiagnostic(nested_errno=True)
    stats = ReceiveStats()
    stats.freeze_terminal()
    assert (
        stats.transport_diagnostic and stats.transport_diagnostic.capture_state == "NOT_APPLICABLE"
    )


@pytest.mark.parametrize("helper", ["response_detail", "build_diagnostic"])
async def test_diagnostic_fault_does_not_turn_success_into_failure(
    monkeypatch: pytest.MonkeyPatch,
    helper: str,
):
    def broken(*args: object) -> object:
        raise RuntimeError("diagnostic helper failure")

    monkeypatch.setattr(receive_stats, helper, broken)
    body = b'data: {"type":"response.completed","response":{"id":"ok","output":[]}}\n\n'
    calls: list[httpx.Request] = []

    def serve(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(200, content=body)

    executor = CodexHttpExecutor(DiagnosticSession(), transport=httpx.MockTransport(serve))
    reply = await executor.dispatch(
        executor.prepare("fixture", {"type": "object"}, output_tokens=6000, timeout_seconds=None)
    )
    assert reply.remote_stop == "COMPLETED" and len(calls) == 1
    assert reply.observation and reply.observation.transport_diagnostic
    assert reply.observation.transport_diagnostic.capture_state == "UNAVAILABLE"
