"""Keep original leaf identity while adding positional, version-bound observations."""

import hashlib
from datetime import UTC, datetime

import pytest

from thoth.adapters.parsers import text_context
from thoth.adapters.parsers.common import node_id
from thoth.adapters.parsers.text_parser import TextParser
from thoth.domain.artifact import ArtifactEnvelope, StructuralDocument
from thoth.domain.context_observation import ContextObservation
from thoth.domain.enums import (
    AuthorityState,
    CutoffState,
    SecurityClass,
    SupportState,
    VerificationState,
)
from thoth.domain.evidence import EvidenceSpan


def parse_text(
    text: str, *, name: str = "case.md", artifact_id: str = "artifact:text"
) -> StructuralDocument:
    raw = text.encode()
    artifact = ArtifactEnvelope(
        artifact_id=artifact_id,
        project_id="project:context",
        source_uri=name,
        media_type="text/plain",
        byte_sha256=hashlib.sha256(raw).hexdigest(),
        authority=AuthorityState.INFORMAL,
        cutoff_state=CutoffState.ELIGIBLE,
        security_class=SecurityClass.INTERNAL,
        retrieved_at=datetime(2026, 10, 26, tzinfo=UTC),
        parser_name="unparsed",
        parser_version="0",
    )
    return TextParser().parse(artifact, raw)


def bound(document: StructuralDocument, version: str = "version:context") -> StructuralDocument:
    observation = document.context_observation
    return document.model_copy(
        update={"context_observation": None if observation is None else observation.bind(version)}
    )


def spans(
    document: StructuralDocument, version: str = "version:context"
) -> tuple[EvidenceSpan, ...]:
    return tuple(
        EvidenceSpan(
            span_id=f"span:{document.artifact.artifact_id}:{n.ordinal:04}",
            project_id=document.artifact.project_id,
            artifact_id=document.artifact.artifact_id,
            source_version_id=version,
            locator=n.locator.model_copy(update={"structural_node_id": n.node_id}),
            exact_text=n.text,
            text_sha256=hashlib.sha256(n.text.encode()).hexdigest(),
            extraction_method=f"text:{document.artifact.parser_version}",
            support_state=SupportState.EXTRACTED,
            authority_state=AuthorityState.INFORMAL,
            verification_state=VerificationState.SCHEMA_VALID,
            cutoff_state=CutoffState.ELIGIBLE,
        )
        for n in document.nodes
        if n.text
    )


def test_fences_observe_preamble_and_heading_without_rewriting_leafs() -> None:
    text = "# Heading\n\nRelease beta\n\n```python\n# code, not heading\n  value = 3\n```\n"
    document = parse_text(text)
    observation = document.context_observation
    assert observation is not None and observation.binding == "DRAFT"
    expected = [(i, line.strip()) for i, line in enumerate(text.splitlines(), 1) if line.strip()]
    assert [(n.locator.line, n.text) for n in document.nodes] == expected
    for ordinal, node in enumerate(document.nodes):
        assert node.node_id == node_id(document.artifact, ordinal, node.kind.value)
    code = next(r for r in observation.regions if r.kind == "FENCED_CODE")
    regions = {r.region_id: r for r in observation.regions}
    assert code.info == "python" and code.closure == "CLOSED"
    assert [regions[k].line_start for k in code.heading_refs] == [1]
    assert [regions[k].line_start for k in code.leading_refs] == [3]
    assert [r.line_start for r in observation.regions if r.kind == "ATX_HEADING"] == [1]
    assert (
        text[code.char_start : code.char_end]
        == "```python\n# code, not heading\n  value = 3\n```\n"
    )


@pytest.mark.parametrize(
    "body,closure",
    [
        ("~~~ text\r\nx\r\n~~~~\r\n", "CLOSED"),
        ("```py\nx\n", "UNCLOSED"),
        ("```", "UNCLOSED"),
        ("```\n", "UNCLOSED"),
        ("~~~\n", "UNCLOSED"),
        ("```\n```\n", "CLOSED"),
        ("> ```\n> x\n> ```\n", "UNKNOWN"),
        ("- note\n\n  ```\n  x\n  ```\n", "UNKNOWN"),
    ],
)
def test_boundaries_crlf_unicode_and_nested_unknown(body: str, closure: str) -> None:
    document = parse_text("\ufeff# 한글\r\n\r\n" + body)
    observation = document.context_observation
    assert observation is not None
    code = next(r for r in observation.regions if r.kind == "FENCED_CODE")
    assert code.closure == closure
    assert code.char_end <= observation.character_count
    assert any(r.kind == "BLANK_INTERVAL" for r in observation.regions)


def test_yaml_path_and_legacy_document_serialization_remain_unchanged() -> None:
    document = parse_text("count:\n  total: 20\n", name="case.yaml")
    assert document.context_observation is None
    assert "context_observation" not in document.model_dump(mode="json")
    assert [n.locator.json_pointer for n in document.nodes] == ["/count", "/count/total"]
    assert document.artifact.parser_version == "1.2.0"


def test_observation_binding_and_corruption_fail_closed() -> None:
    document = bound(parse_text("# Header\n\nLabel\n\n```\nvalue\n```\n"))
    observation = document.context_observation
    assert observation is not None and observation.source_version_id == "version:context"
    with pytest.raises(ValueError, match="CONTEXT_OBSERVATION_ALREADY_BOUND"):
        observation.bind("different-version")
    bad = observation.model_dump(mode="json")
    bad["schema_version"] = "99.0.0"
    with pytest.raises(ValueError):
        ContextObservation.model_validate(bad)
    bad = document.model_dump(mode="json")
    bad["context_observation"]["artifact_id"] = "other"
    with pytest.raises(ValueError, match="CONTEXT_OBSERVATION_SOURCE_MISMATCH"):
        StructuralDocument.model_validate(bad)
    bad = document.model_dump(mode="json")
    bad["context_observation"]["regions"][0]["node_refs"] = ["missing"]
    with pytest.raises(ValueError, match="CONTEXT_OBSERVATION_MEMBER_MISSING"):
        StructuralDocument.model_validate(bad)


def test_unsupported_line_mapping_is_not_reported_as_observed() -> None:
    observation = parse_text("header\u2028value").context_observation
    assert observation is not None and observation.status == "UNAVAILABLE"
    assert not observation.regions and observation.warnings


@pytest.mark.parametrize("name", ["case.md", "case.txt"])
def test_unsupported_observer_version_preserves_leaf_parsing(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = "\ufeff# Header\r\n\r\nLabel\r\n```\r\n  value = 3\r\n```\r\n"
    expected = parse_text(text, name=name)
    monkeypatch.setattr(text_context, "version", lambda _package: "99.0.0")
    actual = parse_text(text, name=name)
    assert actual.artifact == expected.artifact and actual.nodes == expected.nodes
    assert actual.extraction_coverage == expected.extraction_coverage
    observation = actual.context_observation
    assert observation is not None and observation.status == "UNAVAILABLE"
    assert observation.warnings == ("CONTEXT_OBSERVER_VERSION_UNSUPPORTED",)
    assert not observation.regions
    bound_observation = bound(actual).context_observation
    assert bound_observation is not None
    assert bound_observation.source_version_id == "version:context"
