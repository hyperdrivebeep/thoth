from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import AwareDatetime, Field, model_serializer, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.enums import (
    AuthorityState,
    CutoffState,
    ParserErrorCode,
    SecurityClass,
    StructuralNodeKind,
)
from thoth.domain.ids import ArtifactId, ProjectId, Sha256
from thoth.domain.source_time import DocumentTimeObservation, SourceTimeAssessment


class SourceLocator(DomainModel):
    page: int | None = Field(default=None, ge=1)
    line: int | None = Field(default=None, ge=1)
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    section: str | None = None
    paragraph: int | None = Field(default=None, ge=1)
    sheet: str | None = None
    cell_range: str | None = None
    json_pointer: str | None = None
    xml_path: str | None = None
    file_path: str | None = None
    structural_node_id: str | None = None
    bbox: tuple[Decimal, Decimal, Decimal, Decimal] | None = None
    row_index: int | None = Field(default=None, ge=0)
    column_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def exact_char_range(self) -> SourceLocator:
        if (
            self.char_start is not None
            and self.char_end is not None
            and self.char_end < self.char_start
        ):
            raise ValueError("LOCATOR_CHAR_RANGE_INVALID")
        return self

    @model_serializer(mode="wrap")
    def serialize_locator(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        if self.char_start is None:
            data.pop("char_start", None)
        if self.char_end is None:
            data.pop("char_end", None)
        return data


class ParseIssue(DomainModel):
    code: ParserErrorCode
    message: str
    locator: SourceLocator | None = None
    terminal: bool = False


class ArtifactEnvelope(DomainModel):
    artifact_id: ArtifactId
    project_id: ProjectId
    source_uri: str
    media_type: str
    byte_sha256: Sha256
    version_label: str | None = None
    authority: AuthorityState
    cutoff_state: CutoffState
    security_class: SecurityClass
    retrieved_at: AwareDatetime
    parser_name: str
    parser_version: str
    schema_version: str = "1.0.0"


class StructuralRelation(DomainModel):
    kind: Literal[
        "CAPTION_FOR",
        "HEADER_FOR",
        "ROW_FOR",
        "UNIT_FOR",
        "FOOTNOTE_FOR",
        "MENTIONS",
        "CONTINUATION_OF",
    ]
    target_id: str
    basis: Literal["PARSER_OBSERVATION", "EXPLICIT_LABEL", "INFERRED"] = "PARSER_OBSERVATION"


class ParserSelection(DomainModel):
    parser_name: str | None = None
    required_capabilities: tuple[str, ...] = ()


class ParserCapabilityObservation(DomainModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    parser_name: str
    parser_version: str
    source_version_id: str | None = None
    configuration_digest: str | None = None
    asset_manifest_digest: str | None = None
    declared_capabilities: tuple[str, ...] = ()
    extraction_coverage: str
    observed_node_kinds: tuple[str, ...] = ()
    observed_relation_kinds: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    validation_state: Literal["UNVERIFIED", "PARTIAL", "STRUCTURE_VALID"] = "UNVERIFIED"


class StructuralNode(DomainModel):
    node_id: str
    artifact_id: ArtifactId
    kind: StructuralNodeKind
    parent_id: str | None = None
    ordinal: int = Field(ge=0)
    text: str | None = None
    locator: SourceLocator
    extraction_warnings: tuple[str, ...] = ()
    relations: tuple[StructuralRelation, ...] = ()


class StructuralDocument(DomainModel):
    artifact: ArtifactEnvelope
    nodes: tuple[StructuralNode, ...]
    extraction_coverage: str
    warnings: tuple[ParseIssue, ...] = ()
    capability_observation: ParserCapabilityObservation | None = None
    document_time_observations: tuple[DocumentTimeObservation, ...] = ()
    source_time_assessment: SourceTimeAssessment | None = None

    @model_validator(mode="after")
    def exact_structure(self) -> StructuralDocument:
        ids = {node.node_id for node in self.nodes}
        parents = {node.node_id: node.parent_id for node in self.nodes}
        if len(ids) != len(self.nodes):
            raise ValueError("STRUCTURE_NODE_ID_DUPLICATED")
        for node in self.nodes:
            if node.artifact_id != self.artifact.artifact_id:
                raise ValueError("STRUCTURE_ARTIFACT_MISMATCH")
            if node.parent_id is not None and (
                node.parent_id not in ids or node.parent_id == node.node_id
            ):
                raise ValueError("STRUCTURE_PARENT_INVALID")
            if any(relation.target_id not in ids for relation in node.relations):
                raise ValueError("STRUCTURE_RELATION_INVALID")
            seen = {node.node_id}
            parent = node.parent_id
            while parent is not None:
                if parent in seen:
                    raise ValueError("STRUCTURE_PARENT_CYCLE")
                seen.add(parent)
                parent = parents[parent]
        return self
