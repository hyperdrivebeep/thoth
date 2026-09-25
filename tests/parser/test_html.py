from collections.abc import Callable

import pytest

from thoth.adapters.parsers.registry import default_parser_registry
from thoth.domain.artifact import ArtifactEnvelope
from thoth.domain.errors import ParserFailure


@pytest.mark.parametrize("encoding", ["utf-8", "cp949"])
def test_static_html_preserves_korean_table_units_and_footnotes(
    artifact_factory: Callable[[bytes, str, str], ArtifactEnvelope], encoding: str
) -> None:
    raw = (
        "<!doctype html><html><body><h1>조건 비교</h1><table><caption>지연 측정</caption>"
        "<tr><th>조건</th><th>지연(ms)</th></tr><tr><td>alpha</td><td>12</td></tr></table>"
        "<p>각주: 다른 조건에서는 재현되지 않음</p>"
        "<script>forbidden_network_call()</script></body></html>"
    ).encode(encoding)
    parsed = default_parser_registry().parse(artifact_factory(raw, "text/html", ".html"), raw)
    text = "\n".join(n.text or "" for n in parsed.nodes)
    assert "조건 비교" in text and "지연(ms)" in text and "각주" in text
    row = next(n for n in parsed.nodes if n.text and "alpha" in n.text)
    assert row.text and "지연(ms)" in row.text and row.locator.xml_path
    assert "forbidden_network_call" not in text
    assert parsed.warnings
    assert "alpha | 12" in (row.text or "")


@pytest.mark.parametrize(
    "document",
    [
        '<html><body><input type="password"></body></html>',
        '<html><body><div id="root"></div><script>render()</script></body></html>',
        '<html><head><title>Research app</title></head><body><div id="root"></div></body></html>',
        "<html><body><p>Loading...</p></body></html>",
    ],
)
def test_login_and_dynamic_shell_are_not_successful_documents(
    artifact_factory: Callable[[bytes, str, str], ArtifactEnvelope], document: str
) -> None:
    raw = document.encode()
    with pytest.raises(ParserFailure):
        default_parser_registry().parse(artifact_factory(raw, "text/html", ".html"), raw)


@pytest.mark.parametrize(
    "document",
    [
        "<div>Latency is <strong>12 ms</strong>. <span>조건 A</span></div>",
        "<article><div>조건 A: 12 ms</div><div>각주: 재검증 필요</div></article>",
    ],
)
def test_html_fragments_and_div_body_are_readable(
    artifact_factory: Callable[[bytes, str, str], ArtifactEnvelope], document: str
) -> None:
    raw = document.encode()
    parsed = default_parser_registry().parse(artifact_factory(raw, "text/html", ".html"), raw)
    assert "12 ms" in "\n".join(node.text or "" for node in parsed.nodes)
    assert parsed.extraction_coverage == "PARTIAL"


def test_container_qualifiers_survive_nested_paragraphs(
    artifact_factory: Callable[[bytes, str, str], ArtifactEnvelope],
) -> None:
    raw = b"<div>Only under condition A.<p>Latency: 12 ms</p>Not established for condition B.</div>"
    parsed = default_parser_registry().parse(artifact_factory(raw, "text/html", ".html"), raw)
    text = "\n".join(node.text or "" for node in parsed.nodes)
    assert "Only under condition A" in text and "Not established for condition B" in text
    assert text.count("Latency: 12 ms") == 1
