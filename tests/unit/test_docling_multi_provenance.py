from __future__ import annotations

import hashlib
from typing import Any, cast

from tests.fixtures.structured_source_fixture import fixture_export
from tests.unit.test_parser_capability_selection import artifact_for

from thoth.adapters.parsers.docling_structure import convert_structure
from thoth.application.services.research_context_assembler import assemble_context
from thoth.domain.artifact import SourceLocator, StructuralDocument, StructuralNode
from thoth.domain.canonical import canonical_payload
from thoth.domain.enums import (
    AuthorityState,
    CutoffState,
    ParserErrorCode,
    SupportState,
    VerificationState,
)
from thoth.domain.enums import StructuralNodeKind as Kind
from thoth.domain.evidence import EvidenceSpan
from thoth.ports.artifact_ledger import ArtifactLedgerPort


def _convert(export: dict[str, Any]) -> StructuralDocument:
    return convert_structure(
        artifact_for(b"multi-provenance-fixture"),
        export,
        parser_name="fixture",
        parser_version="1",
        configuration_digest="fixture",
        asset_digest="none",
    )


def _single_text_export(text: str, prov: list[dict[str, Any]]) -> dict[str, Any]:
    export = fixture_export()
    export["texts"] = [
        {
            "self_ref": "#/texts/0",
            "parent": {"$ref": "#/body"},
            "label": "text",
            "orig": text,
            "text": text,
            "prov": prov,
        }
    ]
    export["tables"] = []
    return export


def _paragraph_nodes(document: StructuralDocument) -> list[StructuralNode]:
    return [node for node in document.nodes if node.kind == Kind.PARAGRAPH and node.text]


def _prov(start: int, end: int, page: int = 1) -> dict[str, Any]:
    return {
        "page_no": page,
        "bbox": {"l": 10, "t": 20, "r": 100, "b": 40, "coord_origin": "TOPLEFT"},
        "charspan": [start, end],
    }


def test_source_locator_adds_char_offsets_without_legacy_null_payload_drift() -> None:
    assert canonical_payload(SourceLocator(page=1)).decode() == (
        '{"bbox":null,"cell_range":null,"column_index":null,"file_path":null,'
        '"json_pointer":null,"line":null,"page":1,"paragraph":null,'
        '"row_index":null,"section":null,"sheet":null,'
        '"structural_node_id":null,"xml_path":null}'
    )

    locator = SourceLocator(page=1, char_start=4, char_end=9)
    assert locator.char_start == 4
    assert locator.char_end == 9


def test_multi_provenance_text_preserves_segment_locations() -> None:
    text = "first page evidence second page evidence"
    second_start = text.index("second")
    document = _convert(
        _single_text_export(
            text,
            [
                {
                    "page_no": 1,
                    "bbox": {"l": 10, "t": 20, "r": 110, "b": 40, "coord_origin": "TOPLEFT"},
                    "charspan": [0, second_start - 1],
                },
                {
                    "page_no": 2,
                    "bbox": {"l": 10, "t": 50, "r": 130, "b": 70, "coord_origin": "TOPLEFT"},
                    "charspan": [second_start, len(text)],
                },
            ],
        )
    )

    material = _paragraph_nodes(document)
    assert [node.text for node in material] == ["first page evidence", "second page evidence"]
    assert [node.locator.page for node in material] == [1, 2]
    assert [(node.locator.char_start, node.locator.char_end) for node in material] == [
        (0, second_start - 1),
        (second_start, len(text)),
    ]
    assert material[0].locator.bbox is not None
    assert material[1].locator.bbox is not None
    assert material[1].relations[0].kind == "CONTINUATION_OF"
    assert material[1].relations[0].target_id == material[0].node_id


def test_same_page_multiple_boxes_are_not_collapsed() -> None:
    text = "left column right column"
    right_start = text.index("right")
    document = _convert(
        _single_text_export(
            text,
            [
                {
                    "page_no": 1,
                    "bbox": {"l": 10, "t": 20, "r": 80, "b": 40, "coord_origin": "TOPLEFT"},
                    "charspan": [0, right_start - 1],
                },
                {
                    "page_no": 1,
                    "bbox": {"l": 120, "t": 20, "r": 220, "b": 40, "coord_origin": "TOPLEFT"},
                    "charspan": [right_start, len(text)],
                },
            ],
        )
    )

    material = _paragraph_nodes(document)
    assert [node.text for node in material] == ["left column", "right column"]
    assert material[0].locator.page == material[1].locator.page == 1
    assert material[0].locator.bbox != material[1].locator.bbox


def test_missing_charspan_is_partial_not_silent_success() -> None:
    document = _convert(
        _single_text_export(
            "unlocated first unlocated second",
            [
                {
                    "page_no": 1,
                    "bbox": {"l": 10, "t": 20, "r": 80, "b": 40, "coord_origin": "TOPLEFT"},
                },
                {
                    "page_no": 2,
                    "bbox": {"l": 10, "t": 20, "r": 80, "b": 40, "coord_origin": "TOPLEFT"},
                },
            ],
        )
    )

    material = _paragraph_nodes(document)
    assert len(material) == 1
    assert "MULTI_PROVENANCE_UNLOCATED" in material[0].extraction_warnings
    assert material[0].locator.char_start is None
    assert material[0].locator.char_end is None
    assert any(issue.code == ParserErrorCode.PARTIAL_EXTRACTION for issue in document.warnings)


def test_single_provenance_out_of_range_charspan_is_partial_not_observed() -> None:
    document = _convert(_single_text_export("a short exact text", [_prov(0, 999)]))

    material = _paragraph_nodes(document)
    assert document.extraction_coverage == "PARTIAL_STRUCTURE"
    assert any(issue.code == ParserErrorCode.PARTIAL_EXTRACTION for issue in document.warnings)
    assert material[0].locator.page == 1
    assert material[0].locator.bbox is not None
    assert material[0].locator.char_start is None
    assert material[0].locator.char_end is None
    assert "CHARSPAN_UNLOCATED" in material[0].extraction_warnings


def test_orig_text_mismatch_does_not_publish_unverified_char_offsets() -> None:
    export = _single_text_export("stored original text", [_prov(0, 6)])
    export["texts"][0]["text"] = "normalized"
    document = _convert(export)

    material = _paragraph_nodes(document)
    assert document.extraction_coverage == "PARTIAL_STRUCTURE"
    assert material[0].text == "stored original text"
    assert material[0].locator.char_start is None
    assert material[0].locator.char_end is None
    assert "CHARSPAN_UNLOCATED" in material[0].extraction_warnings


def test_mention_promotion_preserves_continuation_relation() -> None:
    export = fixture_export()
    text = "opening located context Table 9 compares alpha timing"
    cut = text.index("Table")
    export["texts"][3].update(
        orig=text, text=text, prov=[_prov(0, cut - 1), _prov(cut, len(text), 2)]
    )

    document = _convert(export)

    segmented = [node for node in document.nodes if node.locator.json_pointer == "#/texts/3"]
    assert [node.text for node in segmented] == [
        "opening located context",
        "Table 9 compares alpha timing",
    ]
    assert segmented[1].kind == Kind.MENTION
    relation_kinds = {relation.kind for relation in segmented[1].relations}
    assert {"CONTINUATION_OF", "MENTIONS"} <= relation_kinds
    assert any(
        relation.kind == "CONTINUATION_OF" and relation.target_id == segmented[0].node_id
        for relation in segmented[1].relations
    )


def test_segmented_caption_anchor_expands_table_context_in_document_order() -> None:
    export = fixture_export()
    text = "Table 9. Response timing continued legend on second page"
    cut = text.index("continued")
    export["texts"][0].update(
        orig=text, text=text, prov=[_prov(0, cut - 1), _prov(cut, len(text), 2)]
    )
    document = _convert(export)
    spans = tuple(
        EvidenceSpan(
            span_id=f"span:probe-{index}",
            project_id=document.artifact.project_id,
            artifact_id=document.artifact.artifact_id,
            source_version_id="source-version:probe",
            locator=node.locator.model_copy(update={"structural_node_id": node.node_id}),
            exact_text=node.text,
            text_sha256=hashlib.sha256(node.text.encode()).hexdigest(),
            extraction_method="fixture",
            support_state=SupportState.EXTRACTED,
            authority_state=AuthorityState.OFFICIAL,
            verification_state=VerificationState.PROVENANCE_VALID,
            cutoff_state=CutoffState.ELIGIBLE,
        )
        for index, node in enumerate(document.nodes)
        if node.text
    )
    anchor = next(span for span in spans if span.exact_text.startswith("continued legend"))

    class Ledger:
        def read_structure(
            self, project: str, artifact: str, version: str
        ) -> StructuralDocument | None:
            assert (project, artifact, version) == (
                document.artifact.project_id,
                document.artifact.artifact_id,
                "source-version:probe",
            )
            return document

    assembly = assemble_context(
        (anchor.span_id,), (anchor,), spans, cast(ArtifactLedgerPort, Ledger())
    )

    selected_text = [span.exact_text for span in assembly.evidence]
    assert selected_text.index("Table 9. Response timing") < selected_text.index(
        "continued legend on second page"
    )
    assert "Time (ms)" in selected_text
    assert "32" in selected_text
    assert {ref.kind for bundle in assembly.bundles for ref in bundle.structures} >= {
        "CAPTION",
        "TABLE",
        "HEADER",
        "CELL",
    }
    assert not any(
        "CONTINUATION_SPAN_UNAVAILABLE" in bundle.limitations for bundle in assembly.bundles
    )
