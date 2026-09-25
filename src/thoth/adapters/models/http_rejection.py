"""Bounded HTTP-rejection metadata. Never persist raw bodies, headers, or secrets."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Literal, cast

import httpx

from thoth.domain.model_dispatch import HttpRejectionMetadata

_MAX_DIAGNOSTIC_BYTES = 1024
_CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_TRANSIENT = frozenset(
    {
        "rate_limit_exceeded",
        "rate_limit",
        "too_many_requests",
        "requests_limit_exceeded",
    }
)
_ACCOUNT = frozenset(
    {
        "insufficient_quota",
        "quota_exceeded",
        "billing_hard_limit_reached",
        "usage_limit_reached",
        "account_deactivated",
    }
)


def _retry_after_seconds(response: httpx.Response) -> int | None:
    raw = response.headers.get("retry-after")
    if raw is None or len(raw) > 64:
        return None
    text = raw.strip()
    if text.isdigit():
        value = int(text)
        return value if 0 <= value <= 86_400 else None
    try:
        instant = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError, IndexError):
        return None
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    delay = round((instant - datetime.now(UTC)).total_seconds())
    if delay < 0:
        return 0
    return delay if delay <= 86_400 else None


def _safe_code(value: object) -> str | None:
    if not isinstance(value, str) or not _CODE_RE.fullmatch(value):
        return None
    return value


def _kind_for(
    status: int, code: str | None
) -> tuple[Literal["TRANSIENT_RATE_LIMIT", "ACCOUNT_LIMIT", "OTHER", "UNKNOWN"], str | None]:
    if code is not None:
        lowered = code.lower()
        if lowered in _TRANSIENT:
            return "TRANSIENT_RATE_LIMIT", f"code:{code}"
        if lowered in _ACCOUNT:
            return "ACCOUNT_LIMIT", f"code:{code}"
        return "OTHER", f"code:{code}"
    if status == 429:
        return "UNKNOWN", "http_status"
    return "OTHER", "http_status"


async def read_rejection_metadata(response: httpx.Response) -> HttpRejectionMetadata:
    status = response.status_code
    retry_after = _retry_after_seconds(response)
    diagnostic_bytes = 0
    capture: Literal["CAPTURED", "PARTIAL", "UNAVAILABLE"] = "UNAVAILABLE"
    code: str | None = None
    try:
        chunks: list[bytes] = []
        async for chunk in response.aiter_bytes():
            if not chunk:
                continue
            remaining = _MAX_DIAGNOSTIC_BYTES - diagnostic_bytes
            if remaining <= 0:
                capture = "PARTIAL"
                break
            take = chunk[:remaining]
            chunks.append(take)
            diagnostic_bytes += len(take)
            if len(chunk) > remaining:
                capture = "PARTIAL"
                break
        body = b"".join(chunks)
        if capture != "PARTIAL":
            capture = "CAPTURED" if body else "UNAVAILABLE"
        if body:
            try:
                parsed: object = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                capture = "PARTIAL"
            else:
                if isinstance(parsed, dict):
                    payload = cast(dict[object, object], parsed)
                    error = payload.get("error")
                    if isinstance(error, dict):
                        error_fields = cast(dict[object, object], error)
                        code = _safe_code(error_fields.get("code")) or _safe_code(
                            error_fields.get("type")
                        )
                    if code is None:
                        code = _safe_code(payload.get("code")) or _safe_code(
                            payload.get("type")
                        )
                    detail = payload.get("detail")
                    if (
                        code is None
                        and isinstance(detail, str)
                        and "is not supported when using Codex with a ChatGPT account" in detail
                    ):
                        code = "model_not_supported"
                else:
                    capture = "PARTIAL"
    except Exception:
        capture = "UNAVAILABLE"
    kind, basis = _kind_for(status, code)
    if kind == "UNKNOWN" and retry_after is not None:
        kind, basis = "TRANSIENT_RATE_LIMIT", "retry_after"
    return HttpRejectionMetadata(
        http_status=status,
        rejection_kind=kind,
        classification_basis=basis,
        retry_after_seconds=retry_after,
        diagnostic_capture_state=capture,
        diagnostic_bytes=diagnostic_bytes,
    )
