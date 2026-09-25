"""User-selected model controls, independent of credentials and scientific policy."""

from typing import Literal

from pydantic import Field

from thoth.domain.base import DomainModel


class ModelSelection(DomainModel):
    provider: str | None = Field(default=None, min_length=1, max_length=160)
    model: str | None = Field(default=None, min_length=1, max_length=160)
    reasoning_effort: str | None = Field(default=None, min_length=1, max_length=32)

    def touches_route(self) -> bool:
        return self.model is not None or self.resolved_provider() is not None

    def resolved_provider(self) -> str | None:
        if self.provider in {None, "default"}:
            return None
        return self.provider


class ModelOption(DomainModel):
    provider: str
    model: str
    reasoning_efforts: tuple[str, ...]
    default_effort: str | None = None
    capability_source: str


class ResolvedModelSettings(DomainModel):
    provider: str
    model: str | None
    reasoning_effort: str | None
    source_by_field: dict[str, str]
    capability_source: str
    settings_digest: str


class ModelPreferenceRevision(DomainModel):
    record_kind: Literal["ModelPreferenceRevision"] = "ModelPreferenceRevision"
    schema_version: Literal["2.0.0"] = "2.0.0"
    project_id: str
    thread_id: str | None
    selection: ModelSelection
