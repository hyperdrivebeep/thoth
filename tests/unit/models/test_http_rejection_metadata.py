from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from thoth.adapters.models.http_rejection import read_rejection_metadata


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("header", "expected"),
    [("0", 0), ("86400", 86400), ("86401", None), ("-1", None), ("not-a-date", None)],
)
async def test_retry_after_numeric_limit_and_malformed_fallback(
    header: str, expected: int | None
) -> None:
    metadata = await read_rejection_metadata(
        httpx.Response(429, headers={"retry-after": header})
    )
    assert metadata.retry_after_seconds == expected


@pytest.mark.asyncio
async def test_retry_after_http_dates_use_utc_and_clamp_past() -> None:
    future = datetime.now(UTC) + timedelta(seconds=120)
    aware = format_datetime(future, usegmt=True)
    naive = future.replace(tzinfo=None).strftime("%a, %d %b %Y %H:%M:%S")
    for header in (aware, naive):
        metadata = await read_rejection_metadata(
            httpx.Response(429, headers={"retry-after": header})
        )
        assert metadata.retry_after_seconds is not None
        assert 115 <= metadata.retry_after_seconds <= 121
    past = format_datetime(datetime.now(UTC) - timedelta(days=1), usegmt=True)
    metadata = await read_rejection_metadata(
        httpx.Response(429, headers={"retry-after": past})
    )
    assert metadata.retry_after_seconds == 0


@pytest.mark.asyncio
async def test_bounded_diagnostic_never_exposes_raw_secret() -> None:
    body = b'{"error":{"code":"rate_limit_exceeded"},"secret":"' + b"S" * 1500 + b'"}'
    metadata = await read_rejection_metadata(httpx.Response(429, content=body))
    assert metadata.diagnostic_bytes == 1024
    assert metadata.diagnostic_capture_state == "PARTIAL"
    assert "SSSS" not in str(metadata.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_account_limit_code_is_classified_without_body_storage() -> None:
    metadata = await read_rejection_metadata(
        httpx.Response(429, content=b'{"error":{"code":"insufficient_quota"}}')
    )
    assert metadata.rejection_kind == "ACCOUNT_LIMIT"
    assert metadata.classification_basis == "code:insufficient_quota"
