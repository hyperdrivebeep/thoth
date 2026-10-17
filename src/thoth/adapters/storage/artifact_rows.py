"""Decode stored artifact and evidence rows without opening storage connections."""

from datetime import datetime

import orjson
from sqlalchemy.engine import RowMapping

from thoth.domain.artifact import ArtifactEnvelope, SourceLocator
from thoth.domain.enums import (
    AuthorityState,
    CutoffState,
    SecurityClass,
    SupportState,
    VerificationState,
)
from thoth.domain.evidence import EvidenceSpan


def artifact_from_row(values: RowMapping) -> ArtifactEnvelope:
    return ArtifactEnvelope(
        artifact_id=str(values["artifact_id"]),
        project_id=str(values["project_id"]),
        source_uri=str(values["source_uri"]),
        media_type=str(values["media_type"]),
        byte_sha256=str(values["byte_sha256"]),
        version_label=(None if values["version_label"] is None else str(values["version_label"])),
        authority=AuthorityState(str(values["authority"])),
        cutoff_state=CutoffState(str(values["cutoff_state"])),
        security_class=SecurityClass(str(values["security_class"])),
        retrieved_at=datetime.fromisoformat(str(values["retrieved_at"])),
        parser_name=str(values["parser_name"]),
        parser_version=str(values["parser_version"]),
    )


def evidence_from_row(values: RowMapping) -> EvidenceSpan:
    return EvidenceSpan(
        span_id=str(values["span_id"]),
        project_id=str(values["project_id"]),
        artifact_id=str(values["artifact_id"]),
        source_version_id=str(values["source_version_id"]),
        locator=SourceLocator.model_validate(orjson.loads(str(values["locator_json"]))),
        exact_text=str(values["exact_text"]),
        text_sha256=str(values["text_sha256"]),
        extraction_method=str(values["extraction_method"]),
        support_state=SupportState(str(values["support_state"])),
        authority_state=AuthorityState(str(values["authority_state"])),
        verification_state=VerificationState(str(values["verification_state"])),
        cutoff_state=CutoffState(str(values["cutoff_state"])),
    )
