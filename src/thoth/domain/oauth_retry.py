"""Explicit, request-scoped OAuth HTTP retry. Never a hidden default."""

from typing import Literal

from thoth.domain.base import DomainModel
from thoth.domain.model_dispatch import HttpRejectionMetadata


class OAuthRetryPolicy(DomainModel):
    version: Literal["1.0.0"] = "1.0.0"
    kind: Literal["ONCE_TRANSIENT_429"] = "ONCE_TRANSIENT_429"
    max_additional_transports: Literal[1] = 1


def retry_wait_seconds(metadata: HttpRejectionMetadata | None) -> float:
    if metadata is None or metadata.retry_after_seconds is None:
        return 1.0
    return float(min(metadata.retry_after_seconds, 60))


def allows_once_transient_429(*, provider: str | None, capability_source: str | None) -> bool:
    source = capability_source or ""
    if source.startswith("codex"):
        return True
    return source in {"", "UNREGISTERED_DEFAULT"} and provider in {"codex-oauth", "default"}


def is_approved_transient_429(reason: str, metadata: HttpRejectionMetadata | None) -> bool:
    if reason != "OAUTH_REQUEST_REJECTED_429":
        return False
    if metadata is None:
        return False
    return (
        metadata.http_status == 429
        and metadata.rejection_kind == "TRANSIENT_RATE_LIMIT"
        and metadata.classification_basis is not None
    )
