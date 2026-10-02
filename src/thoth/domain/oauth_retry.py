"""Explicit, request-scoped OAuth HTTP retry. Never a hidden default."""

from typing import Literal

from thoth.domain.base import DomainModel
from thoth.domain.model_dispatch import HttpRejectionMetadata


class OAuthRetryPolicy(DomainModel):
    version: Literal["1.0.0"] = "1.0.0"
    kind: Literal["ONCE_TRANSIENT_429"] = "ONCE_TRANSIENT_429"
    # The most extra sends one model call may use, the transient-429 retry (at most one of them)
    # and the cut-off retries together. Both 1 (the earlier rule) and 2 stay readable.
    max_additional_transports: Literal[1, 2] = 2


# Extra sends one model call may use when nothing narrower is stated.
MAX_RETRIES_PER_CALL = 2


# Cut off in the middle of the stream, slowed to a crawl, stopped at the per-call time limit, or
# generating without end (the runaway limits).
INTERRUPTED_CALL_REASONS = frozenset(
    {
        "OAUTH_TRANSPORT_FAILURE",
        "OAUTH_STALLED_STREAM_REMOTE_STOP_UNKNOWN",
        "OAUTH_DISPATCH_DEADLINE_REMOTE_STOP_UNKNOWN",
        "OAUTH_RUNAWAY_OUTPUT_REMOTE_STOP_UNKNOWN",
    }
)


def is_interrupted_model_call(reason: str) -> bool:
    return reason in INTERRUPTED_CALL_REASONS


def interrupted_retry_delay_seconds(retry_index: int, unit_random: float) -> float:
    """Wait before retry number retry_index (0-based): 2 s, then 4 s, each shaken by up to 25%."""

    base = 2.0 * (2**retry_index)
    return base * (0.75 + 0.5 * min(max(unit_random, 0.0), 1.0))


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
