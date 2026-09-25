"""Research record family and scope are explicit, independent of identifier spelling."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import ValidationError

from thoth.domain.action import ActionPlan
from thoth.domain.action_full import (
    ActionPlanRecord,
    ActionPortfolioRecord,
    ActionRecord,
    AuthorizationEnvelopeRecord,
)
from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.criterion import CriterionCandidate
from thoth.domain.criterion_contract import CriterionContractRecord
from thoth.domain.enums import EntityType
from thoth.domain.hypothesis import HypothesisPortfolio
from thoth.domain.hypothesis_full import (
    HypothesisPortfolioRecord,
    HypothesisRecord,
    PredictionRecord,
)
from thoth.domain.ids import Sha256
from thoth.domain.outcome import OutcomeRecord
from thoth.domain.outcome_full import OutcomeAssessmentRecord, OutcomeSeriesRecord
from thoth.domain.revision import EntitySnapshot, SemanticRevision
from thoth.domain.test_validity import TestValidityAssessment


class ResearchFamily(StrEnum):
    PREDICTION = "PREDICTION_RECORD"
    TEST_VALIDITY = "TEST_VALIDITY_ASSESSMENT"
    CRITERION = "CRITERION_CONTRACT_RECORD"
    LEGACY_CRITERION = "CRITERION_CANDIDATE"
    OUTCOME_OBSERVATION = "OUTCOME_OBSERVATION_RECORD"
    OUTCOME_ASSESSMENT = "OUTCOME_ASSESSMENT_RECORD"
    OUTCOME_SERIES = "OUTCOME_SERIES_RECORD"
    HYPOTHESIS = "HYPOTHESIS_RECORD"
    HYPOTHESIS_PORTFOLIO = "HYPOTHESIS_PORTFOLIO_RECORD"
    LEGACY_HYPOTHESIS_PORTFOLIO = "LEGACY_HYPOTHESIS_PORTFOLIO"
    ACTION = "ACTION_RECORD"
    ACTION_PORTFOLIO = "ACTION_PORTFOLIO_RECORD"
    ACTION_PLAN = "ACTION_PLAN_RECORD"
    AUTHORIZATION = "AUTHORIZATION_RECORD"
    LEGACY_ACTION_PLAN = "LEGACY_ACTION_PLAN"
    UNRESOLVED = "LEGACY_SCHEMA_UNRESOLVED"


class ResearchIdentity(DomainModel):
    project_id: str
    object_id: str | None
    entity_id: str
    aggregate_kind: EntityType
    schema_family: ResearchFamily
    owner_revision: Sha256
    snapshot_id: str
    content_digest: Sha256
    embedded_entity_ids: tuple[str, ...] = ()


class ResearchIdentityError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class DecodedResearch:
    identity: ResearchIdentity
    record: DomainModel | None


_CODECS: tuple[tuple[ResearchFamily, type[DomainModel], EntityType, str], ...] = (
    (ResearchFamily.PREDICTION, PredictionRecord, EntityType.HYPOTHESIS, "prediction_id"),
    (ResearchFamily.TEST_VALIDITY, TestValidityAssessment, EntityType.HYPOTHESIS, "assessment_id"),
    (ResearchFamily.CRITERION, CriterionContractRecord, EntityType.CRITERION, "criterion_id"),
    (ResearchFamily.LEGACY_CRITERION, CriterionCandidate, EntityType.CRITERION, "criterion_id"),
    (ResearchFamily.OUTCOME_OBSERVATION, OutcomeRecord, EntityType.OUTCOME, "outcome_id"),
    (
        ResearchFamily.OUTCOME_ASSESSMENT,
        OutcomeAssessmentRecord,
        EntityType.OUTCOME,
        "outcome_assessment_id",
    ),
    (ResearchFamily.OUTCOME_SERIES, OutcomeSeriesRecord, EntityType.OUTCOME, "outcome_series_id"),
    (ResearchFamily.HYPOTHESIS, HypothesisRecord, EntityType.HYPOTHESIS, "hypothesis_id"),
    (
        ResearchFamily.HYPOTHESIS_PORTFOLIO,
        HypothesisPortfolioRecord,
        EntityType.HYPOTHESIS,
        "portfolio_id",
    ),
    (
        ResearchFamily.LEGACY_HYPOTHESIS_PORTFOLIO,
        HypothesisPortfolio,
        EntityType.HYPOTHESIS,
        "portfolio_id",
    ),
    (ResearchFamily.ACTION, ActionRecord, EntityType.ACTION, "action_id"),
    (ResearchFamily.ACTION_PORTFOLIO, ActionPortfolioRecord, EntityType.ACTION, "portfolio_id"),
    (ResearchFamily.ACTION_PLAN, ActionPlanRecord, EntityType.ACTION, "plan_id"),
    (
        ResearchFamily.AUTHORIZATION,
        AuthorizationEnvelopeRecord,
        EntityType.ACTION,
        "authorization_id",
    ),
    (ResearchFamily.LEGACY_ACTION_PLAN, ActionPlan, EntityType.ACTION, "plan_id"),
)


def decode_research(revision: SemanticRevision, snapshot: EntitySnapshot) -> DecodedResearch:
    if (revision.project_id, revision.entity_id, revision.entity_type, revision.snapshot_id) != (
        snapshot.project_id,
        snapshot.entity_id,
        snapshot.entity_type,
        snapshot.snapshot_id,
    ):
        raise ResearchIdentityError("RESEARCH_SNAPSHOT_IDENTITY_MISMATCH")
    raw = canonical_payload(snapshot.content)
    allowed_digests = {
        domain_digest(kind, "1.0.0", raw) for kind in ("SNAPSHOT", "ENTITY_SNAPSHOT")
    }
    if snapshot.content_digest not in allowed_digests:
        raise ResearchIdentityError("RESEARCH_SNAPSHOT_DIGEST_MISMATCH")
    matches: list[tuple[ResearchFamily, DomainModel]] = []
    for family, model, kind, identifier_field in _CODECS:
        if kind != snapshot.entity_type:
            continue
        try:
            record = model.model_validate(snapshot.content)
        except ValidationError:
            continue
        payload = record.model_dump(mode="python")
        if payload.get("schema_version", "1.0.0") not in {"1.0.0", "1.1.0"}:
            continue
        if payload.get(identifier_field) != revision.entity_id:
            raise ResearchIdentityError("RESEARCH_RECORD_IDENTITY_MISMATCH")
        if "project_id" in payload and payload["project_id"] != revision.project_id:
            raise ResearchIdentityError("RESEARCH_RECORD_PROJECT_MISMATCH")
        identifiers: list[str] = []
        objects: list[str] = []
        if isinstance(record, HypothesisPortfolio):
            identifiers = [item.hypothesis_id for item in record.hypotheses]
            objects = [item.object_id for item in record.hypotheses]
        elif isinstance(record, ActionPlan):
            identifiers = [item.action_id for item in record.alternatives]
            objects = [item.object_id for item in record.alternatives]
        if identifiers and (
            len(identifiers) != len(set(identifiers))
            or revision.entity_id in identifiers
            or any(value != payload.get("object_id") for value in objects)
        ):
            continue
        # Revision envelope metadata is authoritative. Restore/generic revisions retain immutable
        # historical snapshot content; rebind only metadata in this read projection.
        revision_fields = {
            ResearchFamily.CRITERION: "criterion_revision_id",
            ResearchFamily.OUTCOME_ASSESSMENT: "assessment_revision_id",
            ResearchFamily.OUTCOME_SERIES: "series_revision_id",
            ResearchFamily.HYPOTHESIS: "hypothesis_revision_id",
            ResearchFamily.HYPOTHESIS_PORTFOLIO: "portfolio_revision_id",
            ResearchFamily.ACTION: "action_revision_id",
            ResearchFamily.ACTION_PORTFOLIO: "portfolio_revision_id",
            ResearchFamily.ACTION_PLAN: "plan_revision_id",
            ResearchFamily.AUTHORIZATION: "authorization_revision_id",
        }
        if family in revision_fields:
            payload.update(
                {
                    revision_fields[family]: revision.revision_id,
                    "revision_digest": revision.revision_digest,
                    "created_at": revision.created_at,
                    "supersedes_revision_digest": (
                        revision.parent_revision_digests[0]
                        if len(revision.parent_revision_digests) == 1
                        else None
                    ),
                }
            )
            record = model.model_validate(payload)
        matches.append((family, record))
    family, record = matches[0] if len(matches) == 1 else (ResearchFamily.UNRESOLVED, None)
    payload = {} if record is None else record.model_dump(mode="python")
    object_id = payload.get("object_id")
    identity = ResearchIdentity(
        project_id=revision.project_id,
        object_id=object_id if isinstance(object_id, str) else None,
        entity_id=revision.entity_id,
        aggregate_kind=revision.entity_type,
        schema_family=family,
        owner_revision=revision.revision_digest,
        snapshot_id=snapshot.snapshot_id,
        content_digest=snapshot.content_digest,
        embedded_entity_ids=(
            tuple(item.hypothesis_id for item in record.hypotheses)
            if isinstance(record, HypothesisPortfolio)
            else tuple(item.action_id for item in record.alternatives)
            if isinstance(record, ActionPlan)
            else ()
        ),
    )
    return DecodedResearch(identity, record)
