"""Explicit kind/version dispatch; unsupported records are never treated as reviewed."""

from thoth.domain.base import DomainModel
from thoth.domain.evidence_requirements import (
    CoverageAssessment,
    HypothesisSemanticReviewRecord,
    LegacyCoverageAssessment,
    RequirementSetRevision,
    SemanticReviewRecord,
)
from thoth.domain.model_settings import ModelPreferenceRevision
from thoth.domain.post_execution_learning import PostExecutionLearningResult
from thoth.domain.research_request import (
    REQUEST_CODECS,
    CurrentResultManifest,
    CurrentResultManifestV21,
)
from thoth.domain.research_stage import ResearchStageRecord

CODECS: dict[str, type[DomainModel]] = {
    **REQUEST_CODECS,
    **{
        str(model.model_fields["record_kind"].default): model
        for model in (
            ModelPreferenceRevision,
            CoverageAssessment,
            HypothesisSemanticReviewRecord,
            RequirementSetRevision,
            SemanticReviewRecord,
            ResearchStageRecord,
        )
    },
}


VERSIONED_CODECS = {(kind, "2.0.0"): codec for kind, codec in CODECS.items()}
VERSIONED_CODECS[("CoverageAssessment", "2.0.0")] = LegacyCoverageAssessment
VERSIONED_CODECS[("CoverageAssessment", "2.1.0")] = CoverageAssessment
VERSIONED_CODECS[("CoverageAssessment", "2.2.0")] = CoverageAssessment
CODECS["PostExecutionLearningResult"] = PostExecutionLearningResult
VERSIONED_CODECS[("PostExecutionLearningResult", "2.1.0")] = PostExecutionLearningResult
VERSIONED_CODECS[("CurrentResultManifest", "2.1.0")] = CurrentResultManifestV21


def decode_current_result_manifest(
    content: dict[str, object],
) -> CurrentResultManifest | CurrentResultManifestV21:
    record = decode_research_record(content)
    if not isinstance(record, (CurrentResultManifest, CurrentResultManifestV21)):
        raise ValueError("RESEARCH_RESULT_KIND_MISMATCH")
    return record


def decode_research_record(content: dict[str, object]) -> DomainModel:
    kind = str(content.get("record_kind"))
    if kind not in CODECS:
        raise ValueError("RESEARCH_RECORD_KIND_UNSUPPORTED")
    codec = VERSIONED_CODECS.get((kind, str(content.get("schema_version"))))
    if codec is None:
        raise ValueError("RESEARCH_RECORD_VERSION_UNSUPPORTED")
    return codec.model_validate(content)
