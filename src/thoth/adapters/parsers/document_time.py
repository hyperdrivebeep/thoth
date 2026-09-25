from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

from thoth.domain.artifact import StructuralNode
from thoth.domain.source_time import (
    DocumentTimeObservation,
    DocumentTimeParseStatus,
    DocumentTimeRole,
    DocumentTimeSource,
    NormalizedDocumentTime,
    NormalizedDocumentTimeKind,
    TimeObservationLocator,
    TimeObservationLocatorKind,
    resolve_zone,
)

_ISSUED_LABEL = re.compile(
    r"^(?:발행일|공표일|공개일|issued(?:\s+on)?|published(?:\s+on)?|publication\s+date)\s*[:：]\s*(.+)$",  # noqa: RUF001
    re.IGNORECASE,
)
_ISO_DATETIME = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})"
    r"(?:[T ](?P<time>\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?)"
    r"(?P<zone>Z|[+-]\d{2}:?\d{2})?)?$"
)
_PDF_DATE = re.compile(
    r"^D:(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})"
    r"(?:(?P<hour>\d{2})(?P<minute>\d{2})(?P<second>\d{2})?"
    r"(?P<zone>Z|[+-]\d{2}'\d{2}')?)?$"
)


class PdfInfo(Protocol):
    @property
    def creation_date(self) -> object: ...

    @property
    def modification_date(self) -> object: ...


class PdfReaderLike(Protocol):
    @property
    def metadata(self) -> PdfInfo | None: ...

    @property
    def xmp_metadata(self) -> object | None: ...


class HtmlElementLike(Protocol):
    tag: str

    def get(self, key: str, default: str | None = None) -> str | None: ...
    def xpath(self, query: str) -> Sequence[HtmlElementLike]: ...
    def getroottree(self) -> object: ...


@runtime_checkable
class HtmlTreeWithPath(Protocol):
    def getpath(self, element: HtmlElementLike) -> str: ...


def _observation_id(source: DocumentTimeSource, path: str, raw: str) -> str:
    payload = f"{source.value}\0{path}\0{raw}".encode()
    return f"time-obs:{hashlib.sha256(payload).hexdigest()[:24]}"


def parse_declared_datetime(
    raw: str,
) -> tuple[DocumentTimeParseStatus, NormalizedDocumentTime | None]:
    value = raw.strip()
    if not value:
        return DocumentTimeParseStatus.INVALID, None
    match = _ISO_DATETIME.match(value)
    if match is None:
        return DocumentTimeParseStatus.INVALID, None
    year, month, day = (int(part) for part in match.group("date").split("-"))
    try:
        day_value = date(year, month, day)
    except ValueError:
        return DocumentTimeParseStatus.INVALID, None
    clock = match.group("time")
    zone = match.group("zone")
    if clock is None:
        return DocumentTimeParseStatus.AMBIGUOUS, NormalizedDocumentTime(
            kind=NormalizedDocumentTimeKind.DAY, day=day_value
        )
    parts = clock.split(":")
    second = parts[2] if len(parts) > 2 else "0"
    if "." in second:
        whole, fraction = second.split(".", 1)
        microseconds = int(fraction.ljust(6, "0")[:6])
        second = whole
    else:
        microseconds = 0
    hour, minute, second_value = int(parts[0]), int(parts[1]), int(second)
    if zone is None:
        return DocumentTimeParseStatus.AMBIGUOUS, NormalizedDocumentTime(
            kind=NormalizedDocumentTimeKind.DAY, day=day_value
        )
    tzinfo = resolve_zone(zone)
    if tzinfo is None:
        return DocumentTimeParseStatus.AMBIGUOUS, NormalizedDocumentTime(
            kind=NormalizedDocumentTimeKind.DAY, day=day_value
        )
    try:
        instant = datetime(
            year, month, day, hour, minute, second_value, microseconds, tzinfo=tzinfo
        )
    except ValueError:
        return DocumentTimeParseStatus.INVALID, None
    return DocumentTimeParseStatus.VALID, NormalizedDocumentTime(
        kind=NormalizedDocumentTimeKind.INSTANT, instant=instant.astimezone(UTC)
    )


def parse_pdf_date(raw: str) -> tuple[DocumentTimeParseStatus, NormalizedDocumentTime | None]:
    value = raw.strip()
    match = _PDF_DATE.match(value)
    if match is None:
        return parse_declared_datetime(value)
    try:
        day_value = date(
            int(match.group("year")), int(match.group("month")), int(match.group("day"))
        )
    except ValueError:
        return DocumentTimeParseStatus.INVALID, None
    if match.group("hour") is None:
        return DocumentTimeParseStatus.AMBIGUOUS, NormalizedDocumentTime(
            kind=NormalizedDocumentTimeKind.DAY, day=day_value
        )
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or "0")
    second = int(match.group("second") or "0")
    zone_raw = match.group("zone")
    if zone_raw is None:
        return DocumentTimeParseStatus.AMBIGUOUS, NormalizedDocumentTime(
            kind=NormalizedDocumentTimeKind.DAY, day=day_value
        )
    if zone_raw == "Z":
        tzinfo = UTC
    else:
        sign = 1 if zone_raw[0] == "+" else -1
        hours = int(zone_raw[1:3])
        minutes = int(zone_raw[4:6])
        tzinfo = timezone(sign * timedelta(hours=hours, minutes=minutes))
    try:
        instant = datetime(
            day_value.year, day_value.month, day_value.day, hour, minute, second, tzinfo=tzinfo
        )
    except ValueError:
        return DocumentTimeParseStatus.INVALID, None
    return DocumentTimeParseStatus.VALID, NormalizedDocumentTime(
        kind=NormalizedDocumentTimeKind.INSTANT, instant=instant.astimezone(UTC)
    )


def _from_datetime(
    value: object,
) -> tuple[str, DocumentTimeParseStatus, NormalizedDocumentTime | None]:
    if isinstance(value, datetime):
        raw = value.isoformat()
        if value.tzinfo is None or value.utcoffset() is None:
            return (
                raw,
                DocumentTimeParseStatus.AMBIGUOUS,
                NormalizedDocumentTime(kind=NormalizedDocumentTimeKind.DAY, day=value.date()),
            )
        return (
            raw,
            DocumentTimeParseStatus.VALID,
            NormalizedDocumentTime(
                kind=NormalizedDocumentTimeKind.INSTANT, instant=value.astimezone(UTC)
            ),
        )
    if isinstance(value, date):
        return (
            value.isoformat(),
            DocumentTimeParseStatus.AMBIGUOUS,
            NormalizedDocumentTime(kind=NormalizedDocumentTimeKind.DAY, day=value),
        )
    raw = str(value).strip()
    status, normalized = parse_pdf_date(raw)
    return raw, status, normalized


def extract_pdf_time_observations(reader: PdfReaderLike) -> tuple[DocumentTimeObservation, ...]:
    observations: list[DocumentTimeObservation] = []
    info = reader.metadata
    if info is not None:
        for attr, source, role, path in (
            (
                "creation_date",
                DocumentTimeSource.PDF_INFO_CREATION,
                DocumentTimeRole.CREATED,
                "/CreationDate",
            ),
            (
                "modification_date",
                DocumentTimeSource.PDF_INFO_MODIFICATION,
                DocumentTimeRole.MODIFIED,
                "/ModDate",
            ),
        ):
            value = getattr(info, attr, None)
            if value is None:
                continue
            raw, status, normalized = _from_datetime(value)
            observations.append(
                DocumentTimeObservation(
                    observation_id=_observation_id(source, path, raw),
                    source=source,
                    role=role,
                    raw_value=raw,
                    locator=TimeObservationLocator(
                        kind=TimeObservationLocatorKind.PDF_METADATA, path=path
                    ),
                    parse_status=status,
                    normalized=normalized,
                )
            )
    xmp = reader.xmp_metadata
    if xmp is not None:
        for attr, source, role, path in (
            (
                "xmp_create_date",
                DocumentTimeSource.PDF_XMP_CREATE,
                DocumentTimeRole.CREATED,
                "xmp:CreateDate",
            ),
            (
                "xmp_modify_date",
                DocumentTimeSource.PDF_XMP_MODIFY,
                DocumentTimeRole.MODIFIED,
                "xmp:ModifyDate",
            ),
        ):
            value = getattr(xmp, attr, None)
            if value is None:
                continue
            raw, status, normalized = _from_datetime(value)
            observations.append(
                DocumentTimeObservation(
                    observation_id=_observation_id(source, path, raw),
                    source=source,
                    role=role,
                    raw_value=raw,
                    locator=TimeObservationLocator(
                        kind=TimeObservationLocatorKind.XMP_PROPERTY, path=path
                    ),
                    parse_status=status,
                    normalized=normalized,
                )
            )
    return tuple(observations)


def extract_declared_document_time(
    nodes: Iterable[StructuralNode],
) -> tuple[DocumentTimeObservation, ...]:
    observations: list[DocumentTimeObservation] = []
    for node in nodes:
        text = (node.text or "").strip()
        if not text:
            continue
        match = _ISSUED_LABEL.match(text)
        if match is None:
            continue
        raw = match.group(1).strip()
        status, normalized = parse_declared_datetime(raw)
        if (
            status == DocumentTimeParseStatus.AMBIGUOUS
            and normalized is not None
            and normalized.kind == NormalizedDocumentTimeKind.DAY
            and normalized.timezone is None
        ):
            status = DocumentTimeParseStatus.AMBIGUOUS
        locator = TimeObservationLocator(
            kind=TimeObservationLocatorKind.STRUCTURAL_NODE,
            path=node.node_id,
            page=node.locator.page,
            line=node.locator.line,
            structural_node_id=node.node_id,
        )
        observations.append(
            DocumentTimeObservation(
                observation_id=_observation_id(
                    DocumentTimeSource.DOCUMENT_ISSUED_LABEL, node.node_id, raw
                ),
                source=DocumentTimeSource.DOCUMENT_ISSUED_LABEL,
                role=DocumentTimeRole.ISSUED
                if "발행" in text or "issued" in text.lower()
                else DocumentTimeRole.PUBLISHED,
                raw_value=raw,
                locator=locator,
                parse_status=status,
                normalized=normalized,
            )
        )
    return tuple(observations)


def extract_html_time_observations(root: HtmlElementLike) -> tuple[DocumentTimeObservation, ...]:
    observations: list[DocumentTimeObservation] = []
    tree = root.getroottree()

    def path_of(element: HtmlElementLike) -> str:
        return str(tree.getpath(element)) if isinstance(tree, HtmlTreeWithPath) else element.tag

    for element in root.xpath('//meta[@property="article:published_time"]'):
        raw = (element.get("content") or "").strip()
        if not raw:
            continue
        path = path_of(element)
        status, normalized = parse_declared_datetime(raw)
        observations.append(
            DocumentTimeObservation(
                observation_id=_observation_id(
                    DocumentTimeSource.HTML_ARTICLE_PUBLISHED, path, raw
                ),
                source=DocumentTimeSource.HTML_ARTICLE_PUBLISHED,
                role=DocumentTimeRole.PUBLISHED,
                raw_value=raw,
                locator=TimeObservationLocator(
                    kind=TimeObservationLocatorKind.HTML_ATTRIBUTE, path=path
                ),
                parse_status=status,
                normalized=normalized,
            )
        )
    articles = root.xpath(
        "//*[@itemtype][contains(@itemtype, 'Article')"
        " or contains(@itemtype, 'NewsArticle')"
        " or contains(@itemtype, 'ScholarlyArticle')]"
    )
    scoped = articles if len(articles) == 1 else [root] if not articles else []
    if not scoped:
        return tuple(observations)
    for host in scoped:
        for element in host.xpath('.//*[@itemprop="datePublished"]'):
            raw = (element.get("content") or element.get("datetime") or "").strip()
            if not raw:
                continue
            path = path_of(element)
            status, normalized = parse_declared_datetime(raw)
            observations.append(
                DocumentTimeObservation(
                    observation_id=_observation_id(
                        DocumentTimeSource.HTML_DATE_PUBLISHED, path, raw
                    ),
                    source=DocumentTimeSource.HTML_DATE_PUBLISHED,
                    role=DocumentTimeRole.PUBLISHED,
                    raw_value=raw,
                    locator=TimeObservationLocator(
                        kind=TimeObservationLocatorKind.HTML_ATTRIBUTE, path=path
                    ),
                    parse_status=status,
                    normalized=normalized,
                )
            )
    return tuple(observations)
