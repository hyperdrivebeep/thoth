"""Independent structure case: cross-page caption/note, two tables, native header units."""

import json
from typing import Any

from thoth.adapters.parsers.docling_structure import convert_structure
from thoth.domain.artifact import ArtifactEnvelope, StructuralDocument


def fixture_export() -> dict[str, Any]:
    def text(index: int, value: str, label: str, page: int):
        return {
            "self_ref": f"#/texts/{index}",
            "parent": {"$ref": "#/body"},
            "label": label,
            "text": value,
            "prov": [
                {
                    "page_no": page,
                    "bbox": {
                        "l": 20,
                        "t": 30 + index * 20,
                        "r": 180,
                        "b": 45 + index * 20,
                        "coord_origin": "TOPLEFT",
                    },
                }
            ],
        }

    def table(index: int, caption: int, header: str, value: str):
        return {
            "self_ref": f"#/tables/{index}",
            "parent": {"$ref": "#/body"},
            "label": "table",
            "prov": [
                {
                    "page_no": 2,
                    "bbox": {
                        "l": 20 + index * 250,
                        "t": 100,
                        "r": 200 + index * 250,
                        "b": 200,
                        "coord_origin": "TOPLEFT",
                    },
                }
            ],
            "captions": [{"$ref": f"#/texts/{caption}"}],
            "footnotes": [{"$ref": "#/texts/2"}] if index == 0 else [],
            "data": {
                "num_rows": 2,
                "num_cols": 2,
                "table_cells": [
                    {
                        "start_row_offset_idx": r,
                        "end_row_offset_idx": r + 1,
                        "start_col_offset_idx": c,
                        "end_col_offset_idx": c + 1,
                        "text": v,
                        "column_header": r == 0,
                        "row_header": False,
                        "row_span": 1,
                        "col_span": 1,
                    }
                    for r, c, v in [
                        (0, 0, "Condition"),
                        (0, 1, header),
                        (1, 0, "alpha"),
                        (1, 1, value),
                    ]
                ],
            },
        }

    return {
        "body": {"self_ref": "#/body", "label": "root"},
        "furniture": {"self_ref": "#/furniture", "label": "root"},
        "groups": [],
        "pages": {str(i): {"size": {"height": 600, "width": 600}} for i in (1, 2, 3)},
        "texts": [
            text(0, "Table 9. Response timing", "caption", 1),
            text(1, "Table 10. Independent quality", "caption", 2),
            text(2, "Times use milliseconds; sample excludes warm-up.", "footnote", 3),
            text(3, "Table 9 compares alpha timing after warm-up.", "text", 1),
        ],
        "tables": [table(0, 0, "Time (ms)", "32"), table(1, 1, "Quality", "0.91")],
    }


class StructuredFixtureParser:
    name = "independent-layout-json"
    version = "1.0.0"
    media_types = frozenset({"application/json"})
    suffixes = frozenset({".json"})
    capabilities = frozenset({"TEXT", "TABLE", "CAPTION", "HEADER", "UNIT", "FOOTNOTE", "MENTION"})

    def parse(self, artifact: ArtifactEnvelope, raw: bytes) -> StructuralDocument:
        return convert_structure(
            artifact,
            json.loads(raw),
            parser_name=self.name,
            parser_version=self.version,
            configuration_digest="fixture-config",
            asset_digest="no-model-assets",
        )
