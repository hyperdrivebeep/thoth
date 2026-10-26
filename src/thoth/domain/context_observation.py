"""Version-bound positional context; not semantic evidence or a replacement for leaf text."""

from typing import Literal

from pydantic import Field, model_validator

from thoth.domain.base import DomainModel


class ContextRegion(DomainModel):
    region_id: str
    kind: Literal["ATX_HEADING", "FENCED_CODE", "PARAGRAPH", "BLANK_INTERVAL"]
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=2)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    coordinate_basis: Literal["DECODED_FILE_CODEPOINTS"] = "DECODED_FILE_CODEPOINTS"
    node_refs: tuple[str, ...] = ()
    heading_refs: tuple[str, ...] = ()
    leading_refs: tuple[str, ...] = ()
    container_path: tuple[str, ...] = ()
    heading_level: int | None = Field(default=None, ge=1, le=6)
    marker: str = ""
    info: str = ""
    closure: Literal["CLOSED", "UNCLOSED", "UNKNOWN", "NOT_APPLICABLE"] = "NOT_APPLICABLE"
    relation_basis: Literal["OBSERVED_POSITION"] = "OBSERVED_POSITION"

    @model_validator(mode="after")
    def valid_range(self) -> "ContextRegion":
        if self.line_end <= self.line_start or self.char_end < self.char_start:
            raise ValueError("CONTEXT_REGION_RANGE_INVALID")
        if len(set(self.node_refs)) != len(self.node_refs):
            raise ValueError("CONTEXT_REGION_MEMBER_DUPLICATED")
        if (self.kind == "ATX_HEADING") != (self.heading_level is not None):
            raise ValueError("CONTEXT_HEADING_LEVEL_INVALID")
        return self


class ContextObservation(DomainModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    observer_version: Literal["text-context:1.0.0/markdown-it-py:4.2.0"] = (
        "text-context:1.0.0/markdown-it-py:4.2.0"
    )
    project_id: str
    artifact_id: str
    byte_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_version_id: str | None = None
    binding: Literal["DRAFT", "VERSION_BOUND"] = "DRAFT"
    status: Literal["OBSERVED", "UNAVAILABLE"] = "OBSERVED"
    decoding: Literal["utf-8-sig"] = "utf-8-sig"
    newline_handling: Literal["preserve"] = "preserve"
    line_count: int = Field(ge=0)
    character_count: int = Field(ge=0)
    regions: tuple[ContextRegion, ...] = ()
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid_observation(self) -> "ContextObservation":
        if (self.binding == "VERSION_BOUND") != (self.source_version_id is not None):
            raise ValueError("CONTEXT_OBSERVATION_BINDING_INVALID")
        regions = {r.region_id: r for r in self.regions}
        if len(regions) != len(self.regions):
            raise ValueError("CONTEXT_REGION_ID_DUPLICATED")
        for region in self.regions:
            if region.line_end > self.line_count + 1 or region.char_end > self.character_count:
                raise ValueError("CONTEXT_REGION_OUTSIDE_SOURCE")
            for identifier in (*region.heading_refs, *region.leading_refs):
                target = regions.get(identifier)
                if target is None or target.line_start >= region.line_start:
                    raise ValueError("CONTEXT_RELATION_ORDER_INVALID")
                if identifier in region.heading_refs and target.kind != "ATX_HEADING":
                    raise ValueError("CONTEXT_HEADING_REFERENCE_INVALID")
                if identifier in region.leading_refs and target.kind != "PARAGRAPH":
                    raise ValueError("CONTEXT_LEADING_REFERENCE_INVALID")
                if identifier in region.leading_refs and (
                    target.container_path != region.container_path
                    or target.line_end > region.line_start
                ):
                    raise ValueError("CONTEXT_LEADING_SCOPE_INVALID")
                if (
                    identifier in region.heading_refs
                    and region.container_path[: len(target.container_path)] != target.container_path
                ):
                    raise ValueError("CONTEXT_HEADING_SCOPE_INVALID")
        return self

    def bind(self, source_version_id: str) -> "ContextObservation":
        if self.binding == "VERSION_BOUND":
            if self.source_version_id != source_version_id:
                raise ValueError("CONTEXT_OBSERVATION_ALREADY_BOUND")
            return self
        return ContextObservation.model_validate(
            {
                **self.model_dump(),
                "source_version_id": source_version_id,
                "binding": "VERSION_BOUND",
            }
        )
