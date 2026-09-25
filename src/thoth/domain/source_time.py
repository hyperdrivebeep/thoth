from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, Field

from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import CutoffState
from thoth.domain.ids import ProjectId, Sha256


class DocumentTimeSource(StrEnum):
    PDF_INFO_CREATION = "PDF_INFO_CREATION"
    PDF_INFO_MODIFICATION = "PDF_INFO_MODIFICATION"
    PDF_XMP_CREATE = "PDF_XMP_CREATE"
    PDF_XMP_MODIFY = "PDF_XMP_MODIFY"
    DOCUMENT_ISSUED_LABEL = "DOCUMENT_ISSUED_LABEL"
    HTML_ARTICLE_PUBLISHED = "HTML_ARTICLE_PUBLISHED"
    HTML_DATE_PUBLISHED = "HTML_DATE_PUBLISHED"


class DocumentTimeRole(StrEnum):
    ISSUED = "ISSUED"
    PUBLISHED = "PUBLISHED"
    CREATED = "CREATED"
    MODIFIED = "MODIFIED"
    UNRESOLVED = "UNRESOLVED"


class DocumentTimeParseStatus(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    AMBIGUOUS = "AMBIGUOUS"


class TimeObservationLocatorKind(StrEnum):
    PDF_METADATA = "PDF_METADATA"
    XMP_PROPERTY = "XMP_PROPERTY"
    STRUCTURAL_NODE = "STRUCTURAL_NODE"
    HTML_ATTRIBUTE = "HTML_ATTRIBUTE"


class SourceTimeAssessmentMode(StrEnum):
    AUTO = "AUTO"
    USER_CONFIRMATION = "USER_CONFIRMATION"
    ADVANCED_CORRECTION = "ADVANCED_CORRECTION"


class SourceTimeReasonCode(StrEnum):
    NO_DOCUMENT_DATE = "NO_DOCUMENT_DATE"
    DATE_PARSE_FAILED = "DATE_PARSE_FAILED"
    TIMEZONE_MISSING = "TIMEZONE_MISSING"
    DATE_ROLE_UNRESOLVED = "DATE_ROLE_UNRESOLVED"
    CONFLICTING_DOCUMENT_DATES = "CONFLICTING_DOCUMENT_DATES"
    CUTOFF_INTERVAL_OVERLAP = "CUTOFF_INTERVAL_OVERLAP"
    DATE_EXTRACTION_FAILED = "DATE_EXTRACTION_FAILED"
    CLASSIFIED_ON_OR_BEFORE = "CLASSIFIED_ON_OR_BEFORE"
    CLASSIFIED_AFTER = "CLASSIFIED_AFTER"
    USER_CONFIRMED_ON_OR_BEFORE = "USER_CONFIRMED_ON_OR_BEFORE"
    USER_CONFIRMED_AFTER = "USER_CONFIRMED_AFTER"
    ADVANCED_DATE_CORRECTION = "ADVANCED_DATE_CORRECTION"
    REVERTED_TO_UNKNOWN = "REVERTED_TO_UNKNOWN"


class SourceTimeMutationReason(StrEnum):
    SOURCE_TIME_BASIS_STALE = "SOURCE_TIME_BASIS_STALE"
    SOURCE_TIME_VERSION_MISMATCH = "SOURCE_TIME_VERSION_MISMATCH"
    SOURCE_TIME_ALREADY_RESOLVED = "SOURCE_TIME_ALREADY_RESOLVED"
    SOURCE_TIME_CORRECTION_REQUIRED = "SOURCE_TIME_CORRECTION_REQUIRED"
    SOURCE_TIME_PROHIBITED = "SOURCE_TIME_PROHIBITED"
    SOURCE_TIME_UNKNOWN_REQUIRED = "SOURCE_TIME_UNKNOWN_REQUIRED"


class SourceTimeAssertion(StrEnum):
    ON_OR_BEFORE_CUTOFF = "ON_OR_BEFORE_CUTOFF"
    AFTER_CUTOFF = "AFTER_CUTOFF"


class NormalizedDocumentTimeKind(StrEnum):
    INSTANT = "INSTANT"
    DAY = "DAY"


class TimeObservationLocator(DomainModel):
    kind: TimeObservationLocatorKind
    path: str
    page: int | None = Field(default=None, ge=1)
    line: int | None = Field(default=None, ge=1)
    structural_node_id: str | None = None


class NormalizedDocumentTime(DomainModel):
    kind: NormalizedDocumentTimeKind
    instant: AwareDatetime | None = None
    day: date | None = None
    timezone: str | None = None


class DocumentTimeObservation(DomainModel):
    observation_id: str
    source: DocumentTimeSource
    role: DocumentTimeRole
    raw_value: str
    locator: TimeObservationLocator
    parse_status: DocumentTimeParseStatus
    normalized: NormalizedDocumentTime | None = None


class SourceTimeAssessment(DomainModel):
    project_id: ProjectId
    artifact_id: str
    source_version_id: str
    byte_sha256: Sha256
    cutoff_at: AwareDatetime
    cutoff_state: CutoffState
    mode: SourceTimeAssessmentMode
    basis_observation_ids: tuple[str, ...] = ()
    reason_code: SourceTimeReasonCode
    revision: int = Field(ge=0)
    assessment_digest: Sha256
    assessed_at: AwareDatetime
    schema_version: str = "1.0.0"


class SourceTimeMutationBasis(DomainModel):
    project_id: ProjectId
    artifact_id: str
    source_version_id: str
    byte_sha256: Sha256
    expected_project_revision: int = Field(ge=0)
    expected_cutoff_at: AwareDatetime
    expected_assessment_revision: int = Field(ge=0)
    expected_metadata_digest: Sha256


class SourceTimeClassification(DomainModel):
    cutoff_state: CutoffState
    reason_code: SourceTimeReasonCode
    basis_observation_ids: tuple[str, ...] = ()


class SourceTimeError(Exception):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


_CLASSIFY_ROLES = frozenset({DocumentTimeRole.ISSUED, DocumentTimeRole.PUBLISHED})


def assessment_digest(payload: dict[str, object]) -> Sha256:
    return domain_digest("SOURCE_TIME_ASSESSMENT", "1.0.0", canonical_payload(payload))


def build_source_time_assessment(
    *,
    project_id: str,
    artifact_id: str,
    source_version_id: str,
    byte_sha256: str,
    cutoff_at: datetime,
    cutoff_state: CutoffState,
    mode: SourceTimeAssessmentMode,
    basis_observation_ids: tuple[str, ...],
    reason_code: SourceTimeReasonCode,
    revision: int,
    assessed_at: datetime,
) -> SourceTimeAssessment:
    draft: dict[str, object] = {
        "project_id": project_id,
        "artifact_id": artifact_id,
        "source_version_id": source_version_id,
        "byte_sha256": byte_sha256,
        "cutoff_at": cutoff_at,
        "cutoff_state": cutoff_state,
        "mode": mode,
        "basis_observation_ids": basis_observation_ids,
        "reason_code": reason_code,
        "revision": revision,
        "assessed_at": assessed_at,
        "schema_version": "1.0.0",
    }
    return SourceTimeAssessment.model_validate(
        {**draft, "assessment_digest": assessment_digest(draft)}
    )


def utc_microseconds(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")
    return value.astimezone(UTC)


def resolve_zone(value: str) -> timezone | ZoneInfo | None:
    raw = value.strip()
    if not raw:
        return None
    if raw in {"Z", "z", "UTC", "GMT"}:
        return UTC
    compact = raw.replace(":", "")
    if len(compact) == 5 and compact[0] in {"+", "-"} and compact[1:].isdigit():
        sign = 1 if compact[0] == "+" else -1
        hours = int(compact[1:3])
        minutes = int(compact[3:5])
        if hours > 23 or minutes > 59:
            return None
        return timezone(sign * timedelta(hours=hours, minutes=minutes))
    try:
        return ZoneInfo(raw)
    except ZoneInfoNotFoundError:
        return None


def day_interval(value: date, zone_name: str) -> tuple[datetime, datetime] | None:
    zone = resolve_zone(zone_name)
    if zone is None:
        return None
    start = datetime.combine(value, time.min, tzinfo=zone)
    end = datetime.combine(value + timedelta(days=1), time.min, tzinfo=zone) - timedelta(
        microseconds=1
    )
    return utc_microseconds(start), utc_microseconds(end)


def classify_document_time(
    observations: tuple[DocumentTimeObservation, ...],
    cutoff_at: datetime,
    *,
    current_cutoff_state: CutoffState | None = None,
) -> SourceTimeClassification:
    if current_cutoff_state == CutoffState.PROHIBITED_CONTEXT:
        return SourceTimeClassification(
            cutoff_state=CutoffState.PROHIBITED_CONTEXT,
            reason_code=SourceTimeReasonCode.NO_DOCUMENT_DATE,
        )
    cutoff = utc_microseconds(cutoff_at)
    dated = tuple(
        item
        for item in observations
        if item.role in _CLASSIFY_ROLES
        and item.parse_status == DocumentTimeParseStatus.VALID
        and item.normalized is not None
    )
    if not dated:
        if any(item.parse_status == DocumentTimeParseStatus.INVALID for item in observations):
            reason = SourceTimeReasonCode.DATE_PARSE_FAILED
        elif any(
            item.parse_status == DocumentTimeParseStatus.AMBIGUOUS
            or (
                item.normalized is not None
                and item.normalized.kind == NormalizedDocumentTimeKind.DAY
                and not item.normalized.timezone
            )
            for item in observations
        ):
            reason = SourceTimeReasonCode.TIMEZONE_MISSING
        elif observations:
            reason = SourceTimeReasonCode.DATE_ROLE_UNRESOLVED
        else:
            reason = SourceTimeReasonCode.NO_DOCUMENT_DATE
        return SourceTimeClassification(cutoff_state=CutoffState.UNKNOWN_TIME, reason_code=reason)

    instants: list[tuple[str, datetime]] = []
    intervals: list[tuple[str, datetime, datetime]] = []
    for item in dated:
        normalized = item.normalized
        assert normalized is not None
        if normalized.kind == NormalizedDocumentTimeKind.INSTANT:
            if normalized.instant is None:
                return SourceTimeClassification(
                    cutoff_state=CutoffState.UNKNOWN_TIME,
                    reason_code=SourceTimeReasonCode.DATE_PARSE_FAILED,
                    basis_observation_ids=(item.observation_id,),
                )
            instants.append((item.observation_id, utc_microseconds(normalized.instant)))
            continue
        if normalized.day is None or not normalized.timezone:
            return SourceTimeClassification(
                cutoff_state=CutoffState.UNKNOWN_TIME,
                reason_code=SourceTimeReasonCode.TIMEZONE_MISSING,
                basis_observation_ids=(item.observation_id,),
            )
        interval = day_interval(normalized.day, normalized.timezone)
        if interval is None:
            return SourceTimeClassification(
                cutoff_state=CutoffState.UNKNOWN_TIME,
                reason_code=SourceTimeReasonCode.TIMEZONE_MISSING,
                basis_observation_ids=(item.observation_id,),
            )
        intervals.append((item.observation_id, interval[0], interval[1]))

    unique_instants = {moment for _, moment in instants}
    unique_days = {(start, end) for _, start, end in intervals}
    if len(unique_instants) + len(unique_days) > 1:
        return SourceTimeClassification(
            cutoff_state=CutoffState.UNKNOWN_TIME,
            reason_code=SourceTimeReasonCode.CONFLICTING_DOCUMENT_DATES,
            basis_observation_ids=tuple(item.observation_id for item in dated),
        )

    if instants:
        observation_id, moment = instants[0]
        after = moment > cutoff
        return SourceTimeClassification(
            cutoff_state=CutoffState.AFTER_CUTOFF if after else CutoffState.ELIGIBLE,
            reason_code=(
                SourceTimeReasonCode.CLASSIFIED_AFTER
                if after
                else SourceTimeReasonCode.CLASSIFIED_ON_OR_BEFORE
            ),
            basis_observation_ids=(observation_id,),
        )

    observation_id, start, end = intervals[0]
    if end <= cutoff:
        state = CutoffState.ELIGIBLE
        reason = SourceTimeReasonCode.CLASSIFIED_ON_OR_BEFORE
    elif start > cutoff:
        state = CutoffState.AFTER_CUTOFF
        reason = SourceTimeReasonCode.CLASSIFIED_AFTER
    else:
        state = CutoffState.UNKNOWN_TIME
        reason = SourceTimeReasonCode.CUTOFF_INTERVAL_OVERLAP
    return SourceTimeClassification(
        cutoff_state=state,
        reason_code=reason,
        basis_observation_ids=(observation_id,),
    )
