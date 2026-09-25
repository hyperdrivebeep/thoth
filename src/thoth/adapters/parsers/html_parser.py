"""Static acquired HTML only; no network, JavaScript, cookie or browser execution."""

from importlib import import_module
from typing import Protocol, cast

from thoth.adapters.parsers.common import node_id, parser_artifact, validate_byte_hash
from thoth.adapters.parsers.document_time import extract_html_time_observations
from thoth.domain.artifact import (
    ArtifactEnvelope,
    ParseIssue,
    SourceLocator,
    StructuralDocument,
    StructuralNode,
)
from thoth.domain.enums import ParserErrorCode, StructuralNodeKind
from thoth.domain.errors import ParserFailure


class HtmlTree(Protocol):
    def getpath(self, element: "HtmlElement") -> str: ...


class HtmlElement(Protocol):
    tag: str
    sourceline: int | None
    text: str | None
    tail: str | None

    def get(self, key: str, default: str | None = None) -> str | None: ...
    def text_content(self) -> str: ...
    def xpath(self, query: str) -> list["HtmlElement"]: ...
    def drop_tree(self) -> None: ...
    def getroottree(self) -> HtmlTree: ...


class HtmlLibrary(Protocol):
    def HTMLParser(self, *, no_network: bool, encoding: str) -> object: ...
    def fromstring(self, raw: bytes, *, parser: object) -> HtmlElement: ...


class HtmlParser:
    name = "html"
    version = "1.1.0"
    media_types = frozenset({"text/html", "application/xhtml+xml"})
    suffixes = frozenset({".html", ".htm"})

    @staticmethod
    def block_text(element: HtmlElement) -> str:
        blocks = {
            "div",
            "p",
            "li",
            "table",
            "pre",
            "article",
            "section",
            "main",
            "body",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "ul",
            "ol",
            "dl",
            "dt",
            "dd",
            "blockquote",
            "header",
            "footer",
            "nav",
            "aside",
            "figure",
            "figcaption",
        }
        children = element.xpath("./*")
        if not any(child.tag in blocks for child in children):
            return element.text_content()
        parts = [element.text or ""]
        for child in children:
            if child.tag not in blocks:
                parts.append(child.text_content())
            parts.append(child.tail or "")
        return " ".join(parts)

    def parse(self, artifact: ArtifactEnvelope, raw: bytes) -> StructuralDocument:
        validate_byte_hash(artifact, raw)
        library = cast(HtmlLibrary, import_module("lxml.html"))
        try:
            try:
                raw.decode("utf-8-sig")
                encoding = "utf-8"
            except UnicodeDecodeError:
                raw.decode("cp949")
                encoding = "cp949"
            root = library.fromstring(
                raw, parser=library.HTMLParser(no_network=True, encoding=encoding)
            )
        except Exception as exc:
            raise ParserFailure(ParserErrorCode.CORRUPT_DOCUMENT, "HTML parsing failed") from exc
        if root.xpath('//input[translate(@type,"PASSWORD","password")="password"]'):
            raise ParserFailure(ParserErrorCode.PARTIAL_EXTRACTION, "HTML_LOGIN_REQUIRED")
        tree = root.getroottree()
        for element in root.xpath(
            "//script|//style|//template|//noscript|//*[@hidden]|//*[@aria-hidden='true']"
        ):
            element.drop_tree()
        nodes: list[StructuralNode] = []
        elements = root.xpath(
            "//title|//h1|//h2|//h3|//h4|//h5|//h6|//p|//li|//caption|//tr|//pre|//dt|//dd|"
            "//div|//article|//section|//main|//body|//blockquote|//header|//footer|//nav|//aside|//figcaption"
        )
        body_material = False
        for element in elements:
            if element.tag != "tr" and element.xpath("ancestor::tr"):
                continue
            text = " ".join(self.block_text(element).split())
            if not text:
                continue
            # A row keeps cells in order and repeats its table header/caption for context.
            if element.tag == "tr":
                table = element.xpath("ancestor::table[1]")
                headers = [] if not table else table[0].xpath("./caption|.//th")
                cells = element.xpath("./td|./th")
                text = " | ".join(" ".join(c.text_content().split()) for c in cells)
                context = " | ".join(
                    dict.fromkeys(" ".join(h.text_content().split()) for h in headers)
                )
                if context and context != text:
                    text = context + "\n" + text
            if element.tag != "title" and text.casefold().strip(". …") not in {
                "loading",
                "please enable javascript",
                "enable javascript to continue",
                "로딩 중",
                "불러오는 중",
                "403 forbidden",
                "404 not found",
                "access denied",
            }:
                body_material = True
            kind = StructuralNodeKind.ROW if element.tag == "tr" else StructuralNodeKind.PARAGRAPH
            nodes.append(
                StructuralNode(
                    node_id=node_id(artifact, len(nodes), kind.value),
                    artifact_id=artifact.artifact_id,
                    kind=kind,
                    ordinal=len(nodes),
                    text=text,
                    locator=SourceLocator(
                        line=element.sourceline or 1, xml_path=tree.getpath(element)
                    ),
                    extraction_warnings=("STATIC_HTML_ONLY",),
                )
            )
        if not nodes or not body_material:
            raise ParserFailure(ParserErrorCode.PARTIAL_EXTRACTION, "HTML_DYNAMIC_OR_EMPTY")
        return StructuralDocument(
            artifact=parser_artifact(artifact, name=self.name, version=self.version),
            nodes=tuple(nodes),
            extraction_coverage="PARTIAL",
            warnings=(
                ParseIssue(
                    code=ParserErrorCode.PARTIAL_EXTRACTION,
                    message="Static HTML extraction; dynamic/hidden content "
                    "was not executed or verified",
                ),
            ),
            document_time_observations=extract_html_time_observations(root),
        )
