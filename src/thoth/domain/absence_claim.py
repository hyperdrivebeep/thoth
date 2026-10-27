"""Declared absence claims and bounded literal checks, never a global absence certificate."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from thoth.domain.artifact import SourceLocator
from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.evidence_requirements import ReviewProposal
from thoth.domain.ids import Sha256
from thoth.domain.research_reference import RevisionRef

ABSENCE_INSTRUCTION = (
    "Declare absence claims separately from your answer. Copy each claim statement exactly from "
    "the answer or the referenced confirmed finding. Use the exact string you expected to find "
    "in the source, copied in the source's own language and spelling, as the target. "
    "Declare target_language_basis as SOURCE_LITERAL, TRANSLATED or CONCEPT. "
    "Only SOURCE_LITERAL targets are checked by exact case-sensitive matching. "
    "Do not translate targets or remove identifier suffixes. Declare at most twelve claims, "
    "with at most eight hundred characters in each target and statement. "
    "An empty list does not certify the absence of anything in the documents."
)
ABSENCE_RESULT_CONTRACT = (
    "These checks cover declared literal claims within the recorded extracted scopes only. "
    "A matching string contradicts a literal absence claim, not a scientific conclusion. "
    "A missing string does not prove absence from the whole source or from the world. "
    "Keep the original answer unchanged and preserve all existing authority restrictions."
)
CHECKER_VERSION = "literal-absence:1.1.0"
HANGUL_SOURCE_BOUNDARY_VERSION = "hangul-source-suffix:1.0.0"
HANGUL_SOURCE_SUFFIXES = frozenset(
    {
        "은",
        "는",
        "이",
        "가",
        "을",
        "를",
        "에",
        "에서",
        "에게",
        "의",
        "와",
        "과",
        "도",
        "만",
        "로",
        "으로",
        "까지",
        "부터",
        "보다",
        "처럼",
        "에는",
        "에서는",
        "으로는",
        "로는",
        "과는",
        "와는",
        "이나",
        "나",
        "이며",
        "이고",
        "이다",
        "입니다",
        "이라",
        "이라고",
        "라고",
    }
)
type AbsenceCheckerVersion = Literal["literal-absence:1.0.0", "literal-absence:1.1.0"]
MAX_ABSENCE_CLAIMS = 12
MAX_ABSENCE_CHARS = 800
type AbsenceOutcome = Literal["FOUND", "NOT_FOUND_WITHIN_SCOPE", "NOT_CHECKED", "UNSUPPORTED"]


class AbsenceClaim(DomainModel):
    statement: str
    target: str = Field(
        description="Use the exact string you expected to find in the source, copied in the "
        "source's own language and spelling. Do not translate it."
    )
    target_language_basis: Literal["SOURCE_LITERAL", "TRANSLATED", "CONCEPT"]
    kind: Literal["LITERAL", "IDENTIFIER", "CONCEPT"]
    claimed_scope: Literal["MODEL_INPUT", "ALLOWED_EXTRACTED", "ALL_MATERIAL"]
    origin: Literal["ANSWER", "CONFIRMED_FINDING"]
    finding_index: int | None = Field(default=None, ge=0)


class ReviewProposalE2(ReviewProposal):
    absence_claims: tuple[AbsenceClaim, ...] = Field(default=(), description=ABSENCE_INSTRUCTION)


class AbsenceSourceRef(DomainModel):
    project_id: str
    artifact_id: str
    source_version_id: str
    span_id: str
    text_sha256: Sha256
    locator: SourceLocator
    authority_state: str
    cutoff_state: str
    verification_state: str

    @classmethod
    def from_span(cls, span: EvidenceSpan) -> "AbsenceSourceRef":
        return cls.model_validate(span.model_dump(include=set(cls.model_fields)))


class AbsenceScope(DomainModel):
    scope_digest: Sha256
    request_ref: RevisionRef
    kind: Literal["ACTUAL_ROLE_INPUT", "ALLOWED_EXTRACTED_AT_REQUEST"]
    capture_phase: str
    captured_at: AwareDatetime
    role: str | None = None
    stage_ref: RevisionRef | None = None
    policy_ref: str
    cutoff_at: AwareDatetime
    retrieval_policy_digest: Sha256
    members: tuple[AbsenceSourceRef, ...]
    checked_refs: tuple[str, ...]
    scan_complete: bool
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def bound_scope(self) -> "AbsenceScope":
        ids = {s.span_id for s in self.members}
        if len(ids) != len(self.members) or len(set(self.checked_refs)) != len(self.checked_refs):
            raise ValueError("ABSENCE_SCOPE_DUPLICATE")
        if not set(self.checked_refs) <= ids or any(
            s.project_id != self.request_ref.project_id for s in self.members
        ):
            raise ValueError("ABSENCE_SCOPE_BINDING_MISMATCH")
        if self.scan_complete and (set(self.checked_refs) != ids or self.reasons or not ids):
            raise ValueError("ABSENCE_SCOPE_INCOMPLETE")
        if self.stage_ref is not None and self.stage_ref.project_id != self.request_ref.project_id:
            raise ValueError("ABSENCE_STAGE_PROJECT_MISMATCH")
        if self.scope_digest != domain_digest(
            "ABSENCE_SCOPE", "1.0.0", canonical_payload(self.model_dump(exclude={"scope_digest"}))
        ):
            raise ValueError("ABSENCE_SCOPE_DIGEST_MISMATCH")
        return self


class AbsenceScopeResult(DomainModel):
    scope_digest: Sha256
    outcome: AbsenceOutcome
    witness_refs: tuple[str, ...] = ()
    reason: str | None = None


def effective_absence(a: AbsenceOutcome, b: AbsenceOutcome) -> str:
    if a == "FOUND":
        return "FOUND_IN_MODEL_INPUT"
    if b == "FOUND":
        return (
            "FOUND_OUTSIDE_MODEL_INPUT"
            if a == "NOT_FOUND_WITHIN_SCOPE"
            else "FOUND_A_SCOPE_UNCHECKED"
        )
    if a == b == "NOT_FOUND_WITHIN_SCOPE":
        return "NOT_FOUND_WITHIN_SCOPES"
    return "UNSUPPORTED" if "UNSUPPORTED" in (a, b) else "NOT_CHECKED"


class AbsenceClaimAssessment(DomainModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    claim_id: str
    claim: AbsenceClaim
    claim_digest: Sha256
    check_digest: Sha256
    origin_stage_ref: RevisionRef
    origin_pointer: str
    origin_bound: bool
    a_result: AbsenceScopeResult
    b_result: AbsenceScopeResult
    effective: Literal[
        "FOUND_IN_MODEL_INPUT",
        "FOUND_OUTSIDE_MODEL_INPUT",
        "FOUND_A_SCOPE_UNCHECKED",
        "NOT_FOUND_WITHIN_SCOPES",
        "UNSUPPORTED",
        "NOT_CHECKED",
    ]
    warning_code: str | None = None
    injection_state: Literal[
        "NOT_NEEDED", "PENDING", "DELIVERED", "NOT_DELIVERED", "ALREADY_ATTEMPTED"
    ] = "NOT_NEEDED"
    injection_reason: str | None = None


def absence_check_digest(
    fields: dict[str, object], *, checker_version: AbsenceCheckerVersion = CHECKER_VERSION
) -> str:
    payload = {
        k: fields[k]
        for k in (
            "claim_id",
            "claim_digest",
            "origin_stage_ref",
            "origin_pointer",
            "origin_bound",
            "a_result",
            "b_result",
            "effective",
            "warning_code",
        )
    }
    if checker_version == "literal-absence:1.0.0":
        return domain_digest("ABSENCE_CHECK", "1.0.0", canonical_payload(payload))
    return domain_digest(
        "ABSENCE_CHECK",
        "1.1.0",
        canonical_payload(
            {
                **payload,
                "checker_version": checker_version,
                "source_boundary_version": HANGUL_SOURCE_BOUNDARY_VERSION,
                "source_suffixes": sorted(HANGUL_SOURCE_SUFFIXES),
            }
        ),
    )


class AbsenceAssessments(DomainModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    checker_version: AbsenceCheckerVersion = CHECKER_VERSION
    request_ref: RevisionRef
    active_stage_ref: RevisionRef
    prose_check_coverage: Literal["DECLARED_CLAIMS_ONLY"] = "DECLARED_CLAIMS_ONLY"
    scope_sets: tuple[AbsenceScope, ...]
    items: tuple[AbsenceClaimAssessment, ...]
    not_checked_count: int = Field(ge=0)
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def explicit_checks(self) -> "AbsenceAssessments":
        scopes = {s.scope_digest: s for s in self.scope_sets}
        if len(scopes) != len(self.scope_sets) or any(
            s.request_ref != self.request_ref for s in self.scope_sets
        ):
            raise ValueError("ABSENCE_REQUEST_SCOPE_MISMATCH")
        if not any(s.stage_ref == self.active_stage_ref for s in self.scope_sets):
            raise ValueError("ABSENCE_ACTIVE_STAGE_MISSING")
        if len({i.claim_id for i in self.items}) != len(self.items):
            raise ValueError("ABSENCE_CLAIM_DUPLICATE")
        for item in self.items:
            if item.effective != effective_absence(item.a_result.outcome, item.b_result.outcome):
                raise ValueError("ABSENCE_EFFECTIVE_OUTCOME_MISMATCH")
            if item.warning_code != (
                item.effective if item.effective.startswith("FOUND_") else None
            ):
                raise ValueError("ABSENCE_WARNING_MISMATCH")
            if not item.origin_bound and item.effective.startswith("FOUND_"):
                raise ValueError("ABSENCE_ORIGIN_UNBOUND")
            if (
                item.claim_digest
                != domain_digest("ABSENCE_CLAIM", "1.0.0", canonical_payload(item.claim))
                or item.origin_stage_ref.project_id != self.request_ref.project_id
            ):
                raise ValueError("ABSENCE_CLAIM_BINDING_MISMATCH")
            for outcome, kind in (
                (item.a_result, "ACTUAL_ROLE_INPUT"),
                (item.b_result, "ALLOWED_EXTRACTED_AT_REQUEST"),
            ):
                scope = scopes.get(outcome.scope_digest)
                if scope is None or scope.kind != kind:
                    raise ValueError("ABSENCE_RESULT_SCOPE_MISMATCH")
                if kind == "ACTUAL_ROLE_INPUT" and scope.stage_ref != item.origin_stage_ref:
                    raise ValueError("ABSENCE_ORIGIN_STAGE_MISMATCH")
                if not set(outcome.witness_refs) <= set(scope.checked_refs):
                    raise ValueError("ABSENCE_WITNESS_OUTSIDE_SCOPE")
                if (outcome.outcome == "FOUND") != bool(outcome.witness_refs):
                    raise ValueError("ABSENCE_WITNESS_OUTCOME_MISMATCH")
                if outcome.outcome == "NOT_FOUND_WITHIN_SCOPE" and not scope.scan_complete:
                    raise ValueError("ABSENCE_INCOMPLETE_NOT_FOUND_FORBIDDEN")
            if item.check_digest != absence_check_digest(
                item.model_dump(), checker_version=self.checker_version
            ):
                raise ValueError("ABSENCE_CHECK_DIGEST_MISMATCH")
        if self.not_checked_count != sum(
            i.a_result.outcome == "NOT_CHECKED" or i.b_result.outcome == "NOT_CHECKED"
            for i in self.items
        ):
            raise ValueError("ABSENCE_NOT_CHECKED_COUNT_MISMATCH")
        return self


@dataclass
class AbsenceRun:
    """Ephemeral source snapshots, never an independent canonical store."""

    allowed_scope: AbsenceScope
    allowed_spans: tuple[EvidenceSpan, ...]
    actual_spans: tuple[EvidenceSpan, ...] = ()
    actual_captured_at: datetime | None = None
    checked_spans: dict[str, EvidenceSpan] = field(default_factory=dict)
    witness_attempted: bool = False
    catalog_usage_refs: set[str] = field(default_factory=set)
