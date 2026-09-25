import hashlib
import io
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pypdf import PdfWriter

from thoth.adapters.parsers.docling_pdf import DoclingPdfParser
from thoth.adapters.parsers.pdf_parser import PdfParser
from thoth.adapters.parsers.registry import ParserRegistry
from thoth.adapters.parsers.text_parser import TextParser
from thoth.domain.artifact import ArtifactEnvelope, ParserSelection, StructuralDocument
from thoth.domain.enums import AuthorityState, CutoffState, SecurityClass
from thoth.domain.errors import ParserFailure


class TextOnly:
    name = "fixture-text"
    version = "1"
    media_types = frozenset({"application/pdf"})
    suffixes = frozenset({".pdf"})
    capabilities = frozenset({"TEXT"})

    def parse(self, artifact: ArtifactEnvelope, raw: bytes) -> StructuralDocument:
        return StructuralDocument(
            artifact=artifact.model_copy(
                update={"parser_name": self.name, "parser_version": self.version}
            ),
            nodes=(),
            extraction_coverage="NONE",
        )


class Layout(TextOnly):
    name = "fixture-layout"
    capabilities = frozenset({"TEXT", "TABLE"})


def test_same_media_requires_an_explicit_unambiguous_selection() -> None:
    raw = b"%PDF-1.4\nfixture"
    artifact = ArtifactEnvelope(
        artifact_id="a",
        project_id="p",
        source_uri="fixture.pdf",
        media_type="application/pdf",
        byte_sha256=hashlib.sha256(raw).hexdigest(),
        authority=AuthorityState.INFORMAL,
        cutoff_state=CutoffState.ELIGIBLE,
        security_class=SecurityClass.INTERNAL,
        retrieved_at=datetime.now(UTC),
        parser_name="unparsed",
        parser_version="0",
    )
    with pytest.raises(ParserFailure, match="ambiguous"):
        ParserRegistry((TextOnly(), Layout())).parse(artifact, raw)


def artifact_for(raw: bytes, media: str = "application/pdf") -> ArtifactEnvelope:
    return ArtifactEnvelope.model_validate(
        {
            "artifact_id": "a",
            "project_id": "p",
            "source_uri": "fixture.pdf",
            "media_type": media,
            "byte_sha256": hashlib.sha256(raw).hexdigest(),
            "authority": "INFORMAL",
            "cutoff_state": "ELIGIBLE",
            "security_class": "INTERNAL",
            "retrieved_at": datetime.now(UTC),
            "parser_name": "unparsed",
            "parser_version": "0",
        }
    )


def test_selection_is_not_evidence_of_table_support_and_never_silently_falls_back() -> None:
    raw = b"%PDF-1.4\nfixture"
    document = ParserRegistry((TextOnly(), Layout())).parse(
        artifact_for(raw), raw, selection=ParserSelection(required_capabilities=("TABLE",))
    )
    assert document.artifact.parser_name == "fixture-layout"
    assert (
        document.capability_observation
        and document.capability_observation.validation_state == "UNVERIFIED"
    )
    with pytest.raises(ParserFailure, match="assets were not prepared"):
        ParserRegistry((PdfParser(), DoclingPdfParser(None))).parse(
            artifact_for(raw), raw, selection=ParserSelection(parser_name="docling-pdf")
        )


@pytest.mark.parametrize("case", ["corrupt", "encrypted"])
def test_invalid_pdf_has_a_precise_pre_worker_failure(tmp_path: Path, case: str) -> None:
    (tmp_path / "asset-manifest.json").write_text("{}")
    if case == "corrupt":
        raw = b"%PDF-1.4\nbroken"
    else:
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.encrypt("fixture")
        target = io.BytesIO()
        writer.write(target)
        raw = target.getvalue()
    with pytest.raises(ParserFailure) as error:
        DoclingPdfParser(tmp_path).parse(artifact_for(raw), raw)
    assert error.value.code.value == (
        "CORRUPT_DOCUMENT" if case == "corrupt" else "ENCRYPTED_DOCUMENT"
    )


def test_unavailable_table_parser_does_not_block_independent_text() -> None:
    raw = b"Independent text facts remain readable."
    registry = ParserRegistry((DoclingPdfParser(None), TextParser()))
    document = registry.parse(
        artifact_for(raw, "text/plain"),
        raw,
        selection=ParserSelection(required_capabilities=("TEXT",)),
    )
    assert any(node.text == "Independent text facts remain readable." for node in document.nodes)


def test_caption_region_overlap_uses_cell_geometry_but_stays_inferred() -> None:
    from tests.fixtures.structured_source_fixture import fixture_export

    from thoth.adapters.parsers.docling_structure import convert_structure

    export = fixture_export()
    export["texts"][0]["prov"][0] = {
        "page_no": 2,
        "bbox": {"l": 30, "t": 90, "r": 180, "b": 110, "coord_origin": "TOPLEFT"},
    }
    export["tables"][0]["captions"] = []
    export["texts"].append(
        {
            "self_ref": "#/texts/4",
            "parent": {"$ref": "#/body"},
            "label": "caption",
            "text": "Table 9. Duplicate label keeps geometry inferred",
            "prov": [
                {
                    "page_no": 3,
                    "bbox": {
                        "l": 20,
                        "t": 40,
                        "r": 180,
                        "b": 55,
                        "coord_origin": "TOPLEFT",
                    },
                }
            ],
        }
    )
    export["tables"][0]["prov"][0]["bbox"]["t"] = 105
    for cell in export["tables"][0]["data"]["table_cells"]:
        cell["bbox"] = {
            "l": 30,
            "t": 115 + cell["start_row_offset_idx"] * 20,
            "r": 180,
            "b": 130 + cell["start_row_offset_idx"] * 20,
            "coord_origin": "TOPLEFT",
        }
    document = convert_structure(
        artifact_for(b"fixture"),
        export,
        parser_name="fixture",
        parser_version="1",
        configuration_digest="fixture",
        asset_digest="none",
    )
    caption = next(n for n in document.nodes if n.text == "Table 9. Response timing")
    assert len(caption.relations) == 1 and caption.relations[0].basis == "INFERRED"
    assert document.extraction_coverage == "PARTIAL_STRUCTURE"
