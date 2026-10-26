"""Versioned observations of printed repeated rows, not verified experimental conditions."""

from typing import Literal

from pydantic import Field, model_validator

from thoth.domain.artifact import SourceLocator
from thoth.domain.base import DomainModel
from thoth.domain.ids import Sha256

NUMERIC_GRAMMAR_VERSION = "repeated-numeric-row:1.0.0"


class NumericSourceRef(DomainModel):
    span_id: str
    text_sha256: Sha256
    locator: SourceLocator


class NumericPoint(DomainModel):
    source: NumericSourceRef
    size_text: str
    value_text: str
    size: str
    value: str


class NumericSeriesObservation(DomainModel):
    record_kind: Literal["NumericSeriesObservation"] = "NumericSeriesObservation"
    schema_version: Literal["1.0.0"] = "1.0.0"
    grammar_version: Literal["repeated-numeric-row:1.0.0"] = NUMERIC_GRAMMAR_VERSION
    project_id: str
    artifact_id: str
    source_version_id: str
    source_byte_sha256: Sha256
    parser_version: str
    observer_version: str
    label: str
    size_label: str
    unit: str
    context_label: str
    region_ref: str
    source_refs: tuple[NumericSourceRef, ...]
    points: tuple[NumericPoint, ...] = Field(min_length=2)
    series_digest: Sha256
    points_digest: Sha256
    binding_digest: Sha256
    unit_registry_digest: Sha256
    assumptions: tuple[str, ...] = (
        "POSITIONAL_GROUP_NOT_EXPERIMENTAL_EQUIVALENCE",
        "PRINTED_PRECISION_NOT_MEASUREMENT_CERTIFICATION",
    )

    @model_validator(mode="after")
    def points_have_source_refs(self) -> "NumericSeriesObservation":
        refs = {ref.span_id: ref for ref in self.source_refs}
        if len(refs) != len(self.source_refs) or len(
            {p.source.span_id for p in self.points}
        ) != len(self.points):
            raise ValueError("NUMERIC_SOURCE_REF_DUPLICATE")
        if any(refs.get(point.source.span_id) != point.source for point in self.points):
            raise ValueError("NUMERIC_POINT_SOURCE_REF_MISSING")
        return self


class NumericDiagnostic(DomainModel):
    reason: str
    source_refs: tuple[str, ...]
    label: str | None = None
