from typing import cast

from tests.fixtures.structured_source_fixture import fixture_export
from tests.unit.test_parser_capability_selection import artifact_for

from thoth.adapters.parsers.docling_structure import convert_structure
from thoth.domain.artifact import StructuralDocument
from thoth.domain.enums import StructuralNodeKind as Kind


def _record(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in cast(dict[object, object], value))
    return cast(dict[str, object], value)


def _list(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast(list[object], value)


def _integer(value: object) -> int:
    assert type(value) is int
    return value


def _item(value: object, index: int) -> dict[str, object]:
    return _record(_list(value)[index])


def _convert(export: dict[str, object]) -> StructuralDocument:
    return convert_structure(
        artifact_for(b"fixture"),
        export,
        parser_name="fixture",
        parser_version="1",
        configuration_digest="fixture",
        asset_digest="none",
    )


def test_header_for_targets_same_column_cells_not_table() -> None:
    document = _convert(_record(fixture_export()))
    table = next(n for n in document.nodes if n.kind == Kind.TABLE)
    header = next(n for n in document.nodes if n.text == "Time (ms)")
    data = next(n for n in document.nodes if n.text == "32")
    targets = [r.target_id for r in header.relations if r.kind == "HEADER_FOR"]
    assert table.node_id not in targets
    assert data.node_id in targets
    assert any(r.kind == "ROW_FOR" for r in data.relations)


def test_unique_table_label_promotes_inferred_caption_to_explicit() -> None:
    export = _record(fixture_export())
    table = _item(export["tables"], 0)
    table["captions"] = []
    texts = _list(export["texts"])
    export["texts"] = [texts[0], texts[2], texts[3]]
    export["tables"] = [table]
    _list(_item(export["texts"], 0)["prov"])[0] = {
        "page_no": 2,
        "bbox": {"l": 30, "t": 90, "r": 180, "b": 110, "coord_origin": "TOPLEFT"},
    }
    _record(_item(table["prov"], 0)["bbox"])["t"] = 105
    for raw_cell in _list(_record(table["data"])["table_cells"]):
        cell = _record(raw_cell)
        row = _integer(cell["start_row_offset_idx"])
        cell["bbox"] = {
            "l": 30,
            "t": 115 + row * 20,
            "r": 180,
            "b": 130 + row * 20,
            "coord_origin": "TOPLEFT",
        }
    document = _convert(export)
    caption = next(n for n in document.nodes if n.text == "Table 9. Response timing")
    assert caption.relations[0].kind == "CAPTION_FOR"
    assert caption.relations[0].basis == "EXPLICIT_LABEL"


def test_duplicate_table_label_keeps_geometry_inferred() -> None:
    export = _record(fixture_export())
    table = _item(export["tables"], 0)
    table["captions"] = []
    _list(_item(export["texts"], 0)["prov"])[0] = {
        "page_no": 2,
        "bbox": {"l": 30, "t": 90, "r": 180, "b": 110, "coord_origin": "TOPLEFT"},
    }
    _record(_item(table["prov"], 0)["bbox"])["t"] = 105
    for raw_cell in _list(_record(table["data"])["table_cells"]):
        cell = _record(raw_cell)
        row = _integer(cell["start_row_offset_idx"])
        cell["bbox"] = {
            "l": 30,
            "t": 115 + row * 20,
            "r": 180,
            "b": 130 + row * 20,
            "coord_origin": "TOPLEFT",
        }
    _list(export["texts"]).append(
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
    document = _convert(export)
    caption = next(n for n in document.nodes if n.text == "Table 9. Response timing")
    assert caption.relations[0].basis == "INFERRED"
