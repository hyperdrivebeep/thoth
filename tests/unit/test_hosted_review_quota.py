from __future__ import annotations

from datetime import date

import pytest

from thoth.apps.hosted_review_quota import (
    HostedReviewLimits,
    HostedReviewQuota,
    reject_if_openai_exhausted,
    reject_if_project_limit,
    reject_if_research_busy,
    reject_if_upload_exceeds,
)
from thoth.protocol.jsonrpc import RpcApplicationError


def test_quota_rejects_over_limit() -> None:
    limits = HostedReviewLimits(
        max_projects=3,
        max_upload_bytes=100,
        max_file_bytes=50,
        max_openai_requests_per_day=2,
        max_rpc_per_second=2,
        max_concurrent_research=1,
    )
    reject_if_project_limit(2, limits)
    with pytest.raises(RpcApplicationError, match="HOSTED_REVIEW_PROJECT_LIMIT"):
        reject_if_project_limit(3, limits)
    reject_if_upload_exceeds(existing_bytes=10, incoming_bytes=20, limits=limits)
    with pytest.raises(RpcApplicationError, match="HOSTED_REVIEW_FILE_LIMIT"):
        reject_if_upload_exceeds(existing_bytes=0, incoming_bytes=51, limits=limits)
    with pytest.raises(RpcApplicationError, match="HOSTED_REVIEW_UPLOAD_LIMIT"):
        reject_if_upload_exceeds(existing_bytes=80, incoming_bytes=30, limits=limits)
    reject_if_research_busy(0, limits)
    with pytest.raises(RpcApplicationError, match="HOSTED_REVIEW_RESEARCH_LIMIT"):
        reject_if_research_busy(1, limits)
    reject_if_openai_exhausted(1, limits)
    with pytest.raises(RpcApplicationError, match="HOSTED_REVIEW_OPENAI_LIMIT"):
        reject_if_openai_exhausted(2, limits)
    quota = HostedReviewQuota(limits)
    quota.allow_rpc(0.0)
    quota.allow_rpc(0.2)
    with pytest.raises(RpcApplicationError, match="HOSTED_REVIEW_RPC_RATE_LIMIT"):
        quota.allow_rpc(0.4)
    quota.allow_openai(date(2026, 9, 20))
    quota.allow_openai(date(2026, 9, 20))
    with pytest.raises(RpcApplicationError, match="HOSTED_REVIEW_OPENAI_LIMIT"):
        quota.allow_openai(date(2026, 9, 20))
    quota.allow_openai(date(2026, 9, 21))
    assert quota.openai_used == 1
    assert quota.openai_day == "2026-09-21"
