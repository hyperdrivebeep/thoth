from lxml import html

from thoth.adapters.parsers.document_time import (
    extract_declared_document_time,
    extract_html_time_observations,
)
from thoth.domain.artifact import SourceLocator, StructuralNode
from thoth.domain.enums import StructuralNodeKind
from thoth.domain.source_time import DocumentTimeRole, DocumentTimeSource


def _parse_html(raw: bytes) -> html.HtmlElement:
    # The lxml stub leaves this parser untyped; validate its actual result at the boundary.
    parser: object = vars(html)["fromstring"]
    assert callable(parser)
    tree: object = parser(raw)
    assert isinstance(tree, html.HtmlElement)
    return tree


def test_html_publication_attributes_only() -> None:
    raw = (
        b'<html><body><article itemscope itemtype="https://schema.org/Article">'
        b'<meta property="article:published_time" content="2020-01-01T00:00:00Z" />'
        b'<time datetime="2019-01-01">ignore</time>'
        b'<span itemprop="datePublished" content="2020-01-01T00:00:00Z"></span>'
        b"</article></body></html>"
    )
    tree = _parse_html(raw)
    observations = extract_html_time_observations(tree)
    assert {item.source for item in observations} == {
        DocumentTimeSource.HTML_ARTICLE_PUBLISHED,
        DocumentTimeSource.HTML_DATE_PUBLISHED,
    }
    assert all(item.role is DocumentTimeRole.PUBLISHED for item in observations)


def test_unrelated_time_and_filename_dates_are_ignored() -> None:
    raw = (
        b'<html><body><time datetime="2024-12-31">footer</time>'
        b'<p>report-2020-01-01.pdf</p></body></html>'
    )
    tree = _parse_html(raw)
    assert extract_html_time_observations(tree) == ()


def test_issued_label_is_extracted_from_nodes() -> None:
    node = StructuralNode(
        node_id="node:1",
        artifact_id="artifact:1",
        kind=StructuralNodeKind.PARAGRAPH,
        ordinal=0,
        text="Issued: 2018-05-01T00:00:00Z",
        locator=SourceLocator(page=1, line=1),
    )
    observations = extract_declared_document_time((node,))
    assert observations[0].source is DocumentTimeSource.DOCUMENT_ISSUED_LABEL
    assert observations[0].normalized is not None
