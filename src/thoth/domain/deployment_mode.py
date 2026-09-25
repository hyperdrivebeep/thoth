"""Deployment mode for local loopback vs judge-facing hosted review."""

from __future__ import annotations

import os
from enum import StrEnum


class DeploymentMode(StrEnum):
    LOCAL = "LOCAL"
    HOSTED_REVIEW = "HOSTED_REVIEW"


STALE_RUNNING_REASON = "HOSTED_REVIEW_STALE_RUNNING"
STALE_RUNNING_MESSAGE = "이전 호출은 종료가 확인되지 않았습니다. 다시 질문하세요."


def parse_deployment_mode(raw: str | None = None) -> DeploymentMode:
    value = (os.environ.get("THOTH_DEPLOYMENT_MODE", "") if raw is None else raw).strip()
    if not value:
        return DeploymentMode.LOCAL
    try:
        return DeploymentMode(value)
    except ValueError as exc:
        raise RuntimeError("THOTH_DEPLOYMENT_MODE_UNSUPPORTED") from exc
