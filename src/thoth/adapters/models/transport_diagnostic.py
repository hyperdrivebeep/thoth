"""Local, bounded normalization. Never stringify an exception or persist header values."""

import ssl

import httpx

from thoth.domain.model_dispatch import TransportDiagnostic


def _category(error: BaseException) -> str:
    if isinstance(error, ssl.SSLError):
        return "SSL_ERROR"
    if isinstance(error, OSError):
        return "OS_ERROR"
    kind = type(error)
    if kind.__module__.split(".", 1)[0] not in {"httpx", "httpcore", "h11"}:
        return "UNKNOWN"
    if kind.__name__ in {"ReadError", "WriteError", "ConnectError", "CloseError", "NetworkError"}:
        return "NETWORK_ERROR"
    if kind.__name__ in {"ProtocolError", "RemoteProtocolError", "LocalProtocolError"}:
        return "PROTOCOL_ERROR"
    if kind.__name__ in {"DecodingError", "DecodeError"}:
        return "DECODE_ERROR"
    return "UNKNOWN"


def _next(error: BaseException) -> object:
    return error.__cause__ if error.__cause__ is not None else error.__context__


def exception_detail(error: httpx.HTTPError) -> dict[str, object]:
    kind = type(error)
    name = kind.__name__
    verified = (
        kind.__module__.split(".", 1)[0] == "httpx"
        and name.isascii()
        and name.isidentifier()
        and len(name) <= 64
    )
    detail: dict[str, object] = {"httpx_error_type": name if verified else "UNKNOWN"}
    node = _next(error)
    if node is None:
        return detail
    category = "UNKNOWN"
    errno: int | None = None
    visited = {id(error)}
    depth = 0
    state = "COMPLETE"
    while node is not None:
        if id(node) in visited:
            state = "CYCLE"
            break
        if depth == 4:
            state = "DEPTH_LIMIT"
            break
        if not isinstance(node, BaseException):
            state = "UNKNOWN"
            break
        visited.add(id(node))
        depth += 1
        if category == "UNKNOWN":
            category = _category(node)
        if errno is None and isinstance(node, OSError):
            candidate = node.errno
            if type(candidate) is int and -(2**31) <= candidate <= 2**31 - 1:
                errno = candidate
        node = _next(node)
    detail.update(nested_cause_category=category, nested_errno=errno, cause_chain_state=state)
    return detail


def response_detail(response: httpx.Response) -> dict[str, object]:
    version = response.http_version
    raw_encoding = response.headers.get("content-encoding")
    if raw_encoding is None:
        encoding = "ABSENT"
    elif len(raw_encoding) > 256:
        encoding = "OTHER"
    else:
        parts = [part.strip().upper() for part in raw_encoding.split(",")]
        if not parts or any(
            part not in {"IDENTITY", "GZIP", "DEFLATE", "BR", "ZSTD"} for part in parts
        ):
            encoding = "OTHER"
        else:
            encoding = parts[0] if len(parts) == 1 else "MULTIPLE"
    transfer = response.headers.get("transfer-encoding")
    has_length = "content-length" in response.headers
    if transfer is not None and has_length:
        framing = "CONFLICT"
    elif transfer is not None:
        framing = (
            "CHUNKED" if len(transfer) <= 256 and transfer.strip().lower() == "chunked" else "OTHER"
        )
    else:
        framing = "CONTENT_LENGTH" if has_length else "ABSENT"
    return {
        "http_version": version if version in {"HTTP/1.0", "HTTP/1.1", "HTTP/2"} else "UNKNOWN",
        "content_encoding": encoding,
        "body_framing": framing,
    }


def build_diagnostic(fields: dict[str, object], unavailable: bool) -> TransportDiagnostic:
    try:
        partial = (
            fields.get("httpx_error_type") == "UNKNOWN"
            or fields.get("cause_chain_state") in {"DEPTH_LIMIT", "CYCLE", "UNKNOWN"}
            or fields.get("nested_cause_category") == "UNKNOWN"
            or fields.get("http_version") == "UNKNOWN"
        )
        state = (
            "UNAVAILABLE"
            if unavailable
            else "PARTIAL"
            if partial
            else "CAPTURED"
            if fields
            else "NOT_APPLICABLE"
        )
        return TransportDiagnostic.model_validate({**fields, "capture_state": state})
    except Exception:
        return TransportDiagnostic(capture_state="UNAVAILABLE")
