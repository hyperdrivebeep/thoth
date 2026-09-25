"""Parser protocol edge cases without HTML network or PDF filesystem I/O."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from thoth.adapters.parsers.document_time import (
    extract_html_time_observations,
    extract_pdf_time_observations,
)
from thoth.domain.source_time import DocumentTimeParseStatus, DocumentTimeSource


@dataclass
class FakeHtmlElement:
    tag: str
    attributes: dict[str, str]
    children: list[FakeHtmlElement]

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.attributes.get(key, default)

    def xpath(self, query: str) -> list[FakeHtmlElement]:
        return self.children if query.startswith("//meta") else []

    def getroottree(self) -> object:
        return object()  # Compatibility tree has no getpath().


def test_html_tree_without_getpath_keeps_tag_locator_and_stable_id() -> None:
    meta = FakeHtmlElement(
        "meta",
        {"content": "2020-01-01T00:00:00Z"},
        [],
    )
    root = FakeHtmlElement("html", {}, [meta])
    first = extract_html_time_observations(root)
    repeated = extract_html_time_observations(root)
    assert len(first) == 1
    assert first[0].source is DocumentTimeSource.HTML_ARTICLE_PUBLISHED
    assert first[0].locator.path == "meta"
    assert first[0].observation_id == repeated[0].observation_id


@dataclass(frozen=True)
class FakePdfInfo:
    creation_date: datetime
    modification_date: datetime


@dataclass(frozen=True)
class FakePdfReader:
    metadata: FakePdfInfo
    xmp_metadata: None = None


def test_readonly_pdf_metadata_keeps_precision_and_locator() -> None:
    reader = FakePdfReader(
        FakePdfInfo(
            creation_date=datetime(2020, 1, 1, tzinfo=UTC),
            modification_date=datetime(2020, 1, 2),
        )
    )
    observed = extract_pdf_time_observations(reader)
    assert len(observed) == 2
    assert observed[0].locator.path == "/CreationDate"
    assert observed[0].parse_status is DocumentTimeParseStatus.VALID
    assert observed[1].locator.path == "/ModDate"
    assert observed[1].parse_status is DocumentTimeParseStatus.AMBIGUOUS
