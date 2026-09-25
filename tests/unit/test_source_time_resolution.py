from datetime import UTC, date, datetime, timedelta, timezone

from thoth.domain.enums import CutoffState
from thoth.domain.source_time import (
    DocumentTimeObservation,
    DocumentTimeParseStatus,
    DocumentTimeRole,
    DocumentTimeSource,
    NormalizedDocumentTime,
    NormalizedDocumentTimeKind,
    SourceTimeReasonCode,
    TimeObservationLocator,
    TimeObservationLocatorKind,
    classify_document_time,
)

CUTOFF = datetime(2026, 1, 1, tzinfo=UTC)


def _obs(
    *,
    role: DocumentTimeRole = DocumentTimeRole.PUBLISHED,
    status: DocumentTimeParseStatus = DocumentTimeParseStatus.VALID,
    instant: datetime | None = None,
    day: date | None = None,
    timezone_name: str | None = None,
    source: DocumentTimeSource = DocumentTimeSource.HTML_ARTICLE_PUBLISHED,
) -> DocumentTimeObservation:
    normalized = None
    if instant is not None:
        normalized = NormalizedDocumentTime(
            kind=NormalizedDocumentTimeKind.INSTANT, instant=instant
        )
    elif day is not None:
        normalized = NormalizedDocumentTime(
            kind=NormalizedDocumentTimeKind.DAY, day=day, timezone=timezone_name
        )
    return DocumentTimeObservation(
        observation_id="time-obs:test",
        source=source,
        role=role,
        raw_value="raw",
        locator=TimeObservationLocator(kind=TimeObservationLocatorKind.HTML_ATTRIBUTE, path="meta"),
        parse_status=status,
        normalized=normalized,
    )


def test_cutoff_boundary_inclusive() -> None:
    same = classify_document_time((_obs(instant=CUTOFF),), CUTOFF)
    after = classify_document_time((_obs(instant=CUTOFF + timedelta(microseconds=1)),), CUTOFF)
    before = classify_document_time((_obs(instant=CUTOFF - timedelta(microseconds=1)),), CUTOFF)
    assert same.cutoff_state is CutoffState.ELIGIBLE
    assert after.cutoff_state is CutoffState.AFTER_CUTOFF
    assert before.cutoff_state is CutoffState.ELIGIBLE


def test_offset_equivalent_instants() -> None:
    plus9 = datetime(2026, 1, 1, 9, tzinfo=timezone(timedelta(hours=9)))
    result = classify_document_time((_obs(instant=plus9),), CUTOFF)
    assert result.cutoff_state is CutoffState.ELIGIBLE


def test_day_interval_and_overlap() -> None:
    from datetime import date

    eligible = classify_document_time((_obs(day=date(2025, 12, 31), timezone_name="UTC"),), CUTOFF)
    after = classify_document_time((_obs(day=date(2026, 1, 2), timezone_name="UTC"),), CUTOFF)
    overlap = classify_document_time(
        (_obs(day=date(2026, 1, 1), timezone_name="UTC"),), datetime(2026, 1, 1, 12, tzinfo=UTC)
    )
    assert eligible.cutoff_state is CutoffState.ELIGIBLE
    assert after.cutoff_state is CutoffState.AFTER_CUTOFF
    assert overlap.cutoff_state is CutoffState.UNKNOWN_TIME
    assert overlap.reason_code is SourceTimeReasonCode.CUTOFF_INTERVAL_OVERLAP


def test_missing_timezone_and_creation_date_do_not_qualify() -> None:
    from datetime import date

    unknown = classify_document_time((_obs(day=date(2025, 12, 31)),), CUTOFF)
    created = classify_document_time(
        (
            _obs(
                role=DocumentTimeRole.CREATED,
                instant=CUTOFF,
                source=DocumentTimeSource.PDF_INFO_CREATION,
            ),
        ),
        CUTOFF,
    )
    prohibited = classify_document_time(
        (), CUTOFF, current_cutoff_state=CutoffState.PROHIBITED_CONTEXT
    )
    assert unknown.cutoff_state is CutoffState.UNKNOWN_TIME
    assert created.cutoff_state is CutoffState.UNKNOWN_TIME
    assert prohibited.cutoff_state is CutoffState.PROHIBITED_CONTEXT
