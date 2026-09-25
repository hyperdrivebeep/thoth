from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field

from thoth.domain.artifact import ArtifactEnvelope
from thoth.domain.base import DomainModel
from thoth.domain.enums import SecurityClass
from thoth.domain.evidence_graph import EvidenceSourceRecord
from thoth.domain.ids import Sha256


class ExportPlanScope(DomainModel):
    purpose: str
    recipient: str
    trust_boundary: Literal["LOCAL_ONLY", "INTERNAL_AUTHORIZED", "EXTERNAL_PROTECTED"]
    scope_refs: tuple[str, ...] = Field(min_length=1, max_length=10000)
    cutoff: AwareDatetime
    head_set: dict[str, str]
    head_set_digest: Sha256
    baseline_set_digest: Sha256 | None = None
    selection_rules: dict[str, object]
    classification_ceiling: SecurityClass
    rights_policy_ref: str
    privacy_policy_ref: str
    renderers: tuple[str, ...] = ()
    release_boundary: str


class ExportProvenanceBinding(DomainModel):
    ref: str
    kind: str
    digest: Sha256


class FrozenExportResource(DomainModel):
    artifact: ArtifactEnvelope
    source: EvidenceSourceRecord | None = None
    source_bindings: tuple[EvidenceSourceRecord, ...] = ()
    inclusion_mode: Literal["FULL", "EXCLUDED"]
    reason_codes: tuple[str, ...] = ()


class FrozenExportSnapshot(DomainModel):
    project_id: str
    plan_id: str
    plan_version: int
    plan_digest: Sha256
    scope: ExportPlanScope
    selected_revisions: dict[str, str]
    provenance_bindings: tuple[ExportProvenanceBinding, ...]
    resources: tuple[FrozenExportResource, ...]
    schema_version: Literal["1.0.0"] = "1.0.0"


class ExportFile(DomainModel):
    path: str
    digest: Sha256
    size: int = Field(ge=0)


class StagedExportBundle(DomainModel):
    root: str
    artifacts: tuple[ExportFile, ...]
    manifest: dict[str, object]
