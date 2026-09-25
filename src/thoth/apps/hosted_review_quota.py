"""Judge-facing hosted review limits. Secrets are not stored here."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


@dataclass(frozen=True)
class HostedReviewLimits:
    session_ttl_seconds: int = 24 * 60 * 60
    max_concurrent_sessions: int = 8
    max_concurrent_research: int = 1
    max_upload_bytes: int = 200 * 1024 * 1024
    max_file_bytes: int = 64 * 1024 * 1024
    max_projects: int = 3
    max_openai_requests_per_day: int = 200
    max_rpc_per_second: int = 10


def quota_rejected(reason_code: str) -> RpcApplicationError:
    return RpcApplicationError(
        RpcErrorCode.DOMAIN_REJECTED,
        reason_code,
        data={"reason_code": reason_code, "pre_io": True},
    )


def inbox_nbytes(workspace: Path) -> int:
    inbox = workspace / "inbox"
    if not inbox.is_dir():
        return 0
    return sum(path.stat().st_size for path in inbox.rglob("*") if path.is_file())


def reject_if_upload_exceeds(
    *,
    existing_bytes: int,
    incoming_bytes: int,
    limits: HostedReviewLimits | None = None,
) -> None:
    bound = limits or HostedReviewLimits()
    if incoming_bytes > bound.max_file_bytes:
        raise quota_rejected("HOSTED_REVIEW_FILE_LIMIT")
    if existing_bytes + incoming_bytes > bound.max_upload_bytes:
        raise quota_rejected("HOSTED_REVIEW_UPLOAD_LIMIT")


def reject_if_project_limit(count: int, limits: HostedReviewLimits | None = None) -> None:
    bound = limits or HostedReviewLimits()
    if count >= bound.max_projects:
        raise quota_rejected("HOSTED_REVIEW_PROJECT_LIMIT")


def reject_if_research_busy(running: int, limits: HostedReviewLimits | None = None) -> None:
    bound = limits or HostedReviewLimits()
    if running >= bound.max_concurrent_research:
        raise quota_rejected("HOSTED_REVIEW_RESEARCH_LIMIT")


def reject_if_openai_exhausted(used: int, limits: HostedReviewLimits | None = None) -> None:
    bound = limits or HostedReviewLimits()
    if used >= bound.max_openai_requests_per_day:
        raise quota_rejected("HOSTED_REVIEW_OPENAI_LIMIT")


@dataclass
class HostedReviewQuota:
    limits: HostedReviewLimits = field(default_factory=HostedReviewLimits)
    rpc_times: list[float] = field(default_factory=list)
    openai_used: int = 0
    openai_day: str = ""

    def allow_rpc(self, now: float) -> None:
        self.rpc_times = [stamp for stamp in self.rpc_times if now - stamp < 1]
        if len(self.rpc_times) >= self.limits.max_rpc_per_second:
            raise quota_rejected("HOSTED_REVIEW_RPC_RATE_LIMIT")
        self.rpc_times.append(now)

    def allow_openai(self, now: datetime | date | None = None) -> None:
        moment = datetime.now(UTC) if now is None else now
        day = moment.date().isoformat() if isinstance(moment, datetime) else moment.isoformat()
        if self.openai_day != day:
            self.openai_day = day
            self.openai_used = 0
        reject_if_openai_exhausted(self.openai_used, self.limits)
        self.openai_used += 1

    def allow_projects(self, count: int) -> None:
        reject_if_project_limit(count, self.limits)

    def allow_upload(self, existing_bytes: int, incoming_bytes: int) -> None:
        reject_if_upload_exceeds(
            existing_bytes=existing_bytes,
            incoming_bytes=incoming_bytes,
            limits=self.limits,
        )

    def allow_research(self, running: int) -> None:
        reject_if_research_busy(running, self.limits)
