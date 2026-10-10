"""A YAML file's indented lines keep their key path so a block can be read together."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from thoth.adapters.parsers.text_parser import TextParser
from thoth.domain.artifact import ArtifactEnvelope
from thoth.domain.enums import AuthorityState, CutoffState, SecurityClass

_YAML = b"""# comment line
notice: demo
detection:
  criterion: SYN-C-DET
  numerator: 16
  denominator: 20
false_track:
  criterion: SYN-C-FA
  nested:
    deeper: 1
  event_ids:
    - SYN-FT-1
    - SYN-FT-2
"""


def _artifact(raw: bytes, name: str) -> ArtifactEnvelope:
    return ArtifactEnvelope(
        artifact_id="artifact:text",
        project_id="project:1",
        source_uri=name,
        media_type="text/plain",
        byte_sha256=hashlib.sha256(raw).hexdigest(),
        authority=AuthorityState.INFORMAL,
        cutoff_state=CutoffState.ELIGIBLE,
        security_class=SecurityClass.INTERNAL,
        retrieved_at=datetime.now(UTC),
        parser_name="unparsed",
        parser_version="0",
    )


def _pointers(raw: bytes, name: str) -> dict[str, str | None]:
    document = TextParser().parse(_artifact(raw, name), raw)
    return {node.text or "": node.locator.json_pointer for node in document.nodes}


def test_yaml_lines_carry_their_key_path() -> None:
    pointers = _pointers(_YAML, "result.yaml")
    assert pointers["notice: demo"] == "/notice"
    assert pointers["detection:"] == "/detection"
    assert pointers["denominator: 20"] == "/detection/denominator"
    assert pointers["criterion: SYN-C-FA"] == "/false_track/criterion"
    assert pointers["deeper: 1"] == "/false_track/nested/deeper"
    first, second = pointers["- SYN-FT-1"], pointers["- SYN-FT-2"]
    assert first is not None and first.startswith("/false_track/event_ids/")
    assert first != second


def test_comment_lines_and_markdown_files_get_no_key_path() -> None:
    assert _pointers(_YAML, "result.yaml")["# comment line"] is None
    markdown = _pointers(b"Note: this is prose\n  indented: line\n", "notes.md")
    assert set(markdown.values()) == {None}


def test_every_line_stays_its_own_node_in_order() -> None:
    document = TextParser().parse(_artifact(_YAML, "result.yml"), _YAML)
    assert [node.text for node in document.nodes] == [
        line.strip() for line in _YAML.decode().splitlines() if line.strip()
    ]
    assert [node.locator.line for node in document.nodes][:3] == [1, 2, 3]


def test_demo_result_file_denominator_has_its_block_path() -> None:
    path = (
        Path(__file__).parents[2]
        / "examples"
        / "synthetic-radar-demo-v1"
        / "31_RESULT_RAIN_SYNTHETIC.yaml"
    )
    pointers = _pointers(path.read_bytes(), path.name)
    assert pointers["denominator: 20"] == "/detection/denominator"
    assert pointers['value: "0.80"'] == "/detection/value"
