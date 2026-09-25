from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from thoth.adapters.parsers.csv_parser import CsvParser
from thoth.adapters.parsers.docling_pdf import DoclingPdfParser
from thoth.adapters.parsers.docx_parser import DocxParser
from thoth.adapters.parsers.git_parser import GitManifestParser
from thoth.adapters.parsers.html_parser import HtmlParser
from thoth.adapters.parsers.hwpx_parser import HwpxParser
from thoth.adapters.parsers.json_parser import JsonParser
from thoth.adapters.parsers.pdf_parser import PdfParser
from thoth.adapters.parsers.sniff import sniff_media_type
from thoth.adapters.parsers.test_log_parser import TestLogParser
from thoth.adapters.parsers.text_parser import TextParser
from thoth.adapters.parsers.xlsx_parser import XlsxParser
from thoth.domain.artifact import (
    ArtifactEnvelope,
    ParserCapabilityObservation,
    ParserSelection,
    StructuralDocument,
)
from thoth.domain.enums import ParserErrorCode
from thoth.domain.errors import ParserFailure
from thoth.ports.parser import ParserCapabilityPort, ParserPort

_GENERIC_MEDIA_TYPES = frozenset({"", "application/octet-stream", "binary/octet-stream"})
_COMPATIBLE_SNIFF_SETS = {
    frozenset({"text", "csv"}),
    frozenset({"json", "git"}),
    frozenset({"text", "test-log"}),
}


class ParserRegistry:
    def __init__(
        self, parsers: Sequence[ParserPort], *, default_by_media: Mapping[str, str] | None = None
    ) -> None:
        self._parsers = tuple(parsers)
        self._defaults = dict(default_by_media or {})
        if len({parser.name for parser in parsers}) != len(parsers):
            raise ValueError("PARSER_NAME_DUPLICATED")

    def supported_media_types(self) -> Sequence[str]:
        return tuple(sorted({media for parser in self._parsers for media in parser.media_types}))

    def parser_names(self) -> tuple[str, ...]:
        return tuple(sorted(parser.name for parser in self._parsers))

    async def parse_async(
        self,
        artifact: ArtifactEnvelope,
        raw: bytes,
        *,
        source_path: Path | None = None,
        selection: ParserSelection | None = None,
    ) -> StructuralDocument:
        # Only raw bytes and immutable parser input cross this worker boundary, never a DB handle.
        return await asyncio.to_thread(
            self.parse, artifact, raw, source_path=source_path, selection=selection
        )

    def parse(
        self,
        artifact: ArtifactEnvelope,
        raw: bytes,
        *,
        source_path: Path | None = None,
        selection: ParserSelection | None = None,
    ) -> StructuralDocument:
        declared = artifact.media_type.lower().split(";", 1)[0].strip()
        sniffed = sniff_media_type(raw)
        suffix = (
            source_path.suffix.lower()
            if source_path is not None
            else Path(artifact.source_uri).suffix.lower()
        )
        declared_parsers = tuple(p for p in self._parsers if declared in p.media_types)
        sniffed_parsers = tuple(p for p in self._parsers if sniffed in p.media_types)
        suffix_parsers = tuple(p for p in self._parsers if suffix in p.suffixes)

        if declared not in _GENERIC_MEDIA_TYPES and not declared_parsers:
            raise ParserFailure(
                ParserErrorCode.UNSUPPORTED_MEDIA_TYPE,
                f"no parser registered for declared media type {declared!r}",
            )
        if (
            declared_parsers
            and sniffed_parsers
            and all(
                left.name != right.name
                and frozenset({left.name, right.name}) not in _COMPATIBLE_SNIFF_SETS
                for left in declared_parsers
                for right in sniffed_parsers
            )
        ):
            raise ParserFailure(
                ParserErrorCode.MEDIA_TYPE_MISMATCH,
                f"declared {declared!r} conflicts with detected {sniffed!r}",
            )
        candidates = declared_parsers or suffix_parsers or sniffed_parsers
        if not candidates:
            raise ParserFailure(
                ParserErrorCode.UNSUPPORTED_MEDIA_TYPE,
                "document media type could not be selected safely",
            )
        chosen = selection or ParserSelection()
        matches = tuple(
            p
            for p in candidates
            if (chosen.parser_name is None or p.name == chosen.parser_name)
            and set(chosen.required_capabilities) <= self.capabilities(p)
        )
        if not matches:
            raise ParserFailure(
                ParserErrorCode.PARSER_CAPABILITY_UNAVAILABLE,
                "requested parser capability is unavailable",
            )
        default = self._defaults.get(declared) or self._defaults.get(sniffed or "")
        preferred = tuple(p for p in matches if p.name == default)
        if len(matches) > 1 and len(preferred) != 1:
            raise ParserFailure(
                ParserErrorCode.AMBIGUOUS_PARSER_SELECTION, "ambiguous parser selection"
            )
        selected = matches[0] if len(matches) == 1 else preferred[0]
        document = selected.parse(artifact, raw)
        observation = document.capability_observation or ParserCapabilityObservation(
            parser_name=selected.name,
            parser_version=selected.version,
            declared_capabilities=tuple(sorted(self.capabilities(selected))),
            extraction_coverage=document.extraction_coverage,
        )
        observation = observation.model_copy(
            update={
                "observed_node_kinds": tuple(sorted({n.kind.value for n in document.nodes})),
                "observed_relation_kinds": tuple(
                    sorted({r.kind for n in document.nodes for r in n.relations})
                ),
                "warnings": tuple(issue.code.value for issue in document.warnings),
                "validation_state": "UNVERIFIED"
                if not document.nodes
                else "PARTIAL"
                if document.warnings
                else "STRUCTURE_VALID",
            }
        )
        return document.model_copy(update={"capability_observation": observation})

    @staticmethod
    def capabilities(parser: ParserPort) -> frozenset[str]:
        return (
            parser.capabilities if isinstance(parser, ParserCapabilityPort) else frozenset({"TEXT"})
        )

    def _by_media_type(self, media_type: str | None) -> ParserPort | None:
        if media_type is None:
            return None
        return next(
            (parser for parser in self._parsers if media_type in parser.media_types),
            None,
        )

    def _by_suffix(self, suffix: str) -> ParserPort | None:
        return next((parser for parser in self._parsers if suffix in parser.suffixes), None)


def default_parser_registry() -> ParserRegistry:
    asset_root = os.environ.get("THOTH_DOCLING_ASSETS")
    return ParserRegistry(
        (
            HwpxParser(),
            DocxParser(),
            PdfParser(),
            DoclingPdfParser(
                None if asset_root is None else Path(asset_root),
                python=os.environ.get("THOTH_STRUCTURED_PDF_PYTHON"),
            ),
            XlsxParser(),
            GitManifestParser(),
            TestLogParser(),
            JsonParser(),
            CsvParser(),
            TextParser(),
            HtmlParser(),
        ),
        default_by_media={"application/pdf": os.environ.get("THOTH_PDF_PARSER", "pdf")},
    )
