"""A provider's model list as last verified, kept so a failed refresh never empties it.

A snapshot holds only model names, effort names and timestamps. The account is identified by a
one-way digest; tokens, account ids, raw responses and provider diagnostics are never stored.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import AwareDatetime, Field

from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.ids import Sha256
from thoth.domain.model_settings import ModelOption

CatalogSource = Literal["PROVIDER_LIST", "CLI_ALIAS", "CURATED"]
CatalogStatus = Literal["ACTIVE", "STALE_LAST_GOOD", "UNAVAILABLE"]
ExclusionReason = Literal["UNSUPPORTED_SLUG", "UNKNOWN_EFFORT_ONLY", "NAMESPACED_ID"]
ExecutionState = Literal["UNVERIFIED", "VERIFIED", "REJECTED"]

REFRESH_MAX_AGE_SECONDS = 24 * 60 * 60
_REJECTION_CODES = re.compile(
    r"^(OAUTH_MODEL_NOT_AVAILABLE|OAUTH_MODEL_NOT_SUPPORTED_\d{3}|CLAUDE_CODE_MODEL_[A-Z_]+"
    r"|XAI_MODEL_(REJECTED|NOT_AVAILABLE)(_\d{3})?)$"
)


def authority_digest(provider: str, identity: str) -> str:
    """One-way identity of the signed-in account for one provider; the identity is not kept."""

    return domain_digest(
        "MODEL_CATALOG_AUTHORITY",
        "1.0.0",
        canonical_payload({"provider": provider, "identity": identity}),
    )


def is_model_rejection(reason_code: str) -> bool:
    """True only when the provider refused the model or account.

    Network failures, timeouts and quota or budget limits say nothing about the model and never
    count.
    """

    return _REJECTION_CODES.fullmatch(reason_code) is not None


class ExcludedModel(DomainModel):
    model: str = Field(min_length=1, max_length=160)
    reason: ExclusionReason


class ExecutionMark(DomainModel):
    model: str = Field(min_length=1, max_length=160)
    state: Literal["VERIFIED", "REJECTED"]
    reason_code: str | None = Field(default=None, max_length=160)
    observed_at: AwareDatetime


class CatalogProviderStatus(DomainModel):
    """What the picker says about one provider's list: where it came from and how fresh."""

    provider: str
    source: CatalogSource
    status: CatalogStatus
    fetched_at: AwareDatetime | None = None
    failure_reason: str | None = None
    excluded: tuple[ExcludedModel, ...] = ()


class CatalogSnapshot(DomainModel):
    record_kind: Literal["CatalogSnapshot"] = "CatalogSnapshot"
    schema_version: Literal["1.0.0"] = "1.0.0"
    provider: str = Field(min_length=1, max_length=80)
    source: CatalogSource
    authority_digest: Sha256
    fetched_at: AwareDatetime
    validated_at: AwareDatetime
    options: tuple[ModelOption, ...] = Field(max_length=200)
    excluded: tuple[ExcludedModel, ...] = Field(default=(), max_length=200)
    default_model: str | None = Field(default=None, max_length=160)
    status: CatalogStatus = "ACTIVE"
    # Why the newest refresh failed while this older list is being kept.
    failure_reason: str | None = Field(default=None, max_length=160)
    last_attempt_at: AwareDatetime | None = None
    executions: tuple[ExecutionMark, ...] = Field(default=(), max_length=200)

    def effective_options(self) -> tuple[ModelOption, ...]:
        """The options with what real requests showed about each: verified or rejected."""

        marks = {mark.model: mark for mark in self.executions}
        return tuple(
            option.model_copy(update={"execution": marks[option.model].state})
            if option.model in marks
            else option
            for option in self.options
        )
