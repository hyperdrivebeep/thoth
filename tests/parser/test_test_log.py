from __future__ import annotations

import json
from typing import Any

from thoth.adapters.parsers.registry import default_parser_registry
from thoth.domain.enums import ParserErrorCode, StructuralNodeKind


def test_jsonl_test_log_preserves_run_and_malformed_line(artifact_factory: Any) -> None:
    valid = json.dumps(
        {
            "run_id": "run-17",
            "timestamp": "2026-08-30T12:00:00Z",
            "level": "INFO",
            "event": "measurement.completed",
            "metric": "latency",
        }
    )
    raw = f"{valid}\nnot-json\n".encode()
    artifact = artifact_factory(
        raw, "application/vnd.thoth.test-log+jsonl", ".testlog.jsonl"
    )

    document = default_parser_registry().parse(artifact, raw)

    assert document.artifact.parser_name == "test-log"
    assert document.nodes[0].kind == StructuralNodeKind.PARAGRAPH
    assert document.nodes[0].locator.section == "run:run-17"
    assert document.nodes[1].kind == StructuralNodeKind.UNSUPPORTED_ELEMENT
    assert document.warnings[0].code == ParserErrorCode.UNTRUSTED_LOG_LINE
