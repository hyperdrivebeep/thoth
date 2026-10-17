"""Keep scoped source reads, metadata checks and packet ordering unchanged."""

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import cast

import pytest
from tests.integration.test_research_focus_basis import research_host
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.application.services.research_analysis import ResearchAnalysis
from thoth.domain.artifact import ArtifactEnvelope, StructuralDocument
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.evidence_graph import SpanCorrectionRecord
from thoth.domain.governance import SourceBinding
from thoth.domain.resource_scope import ResourceScopeError


@pytest.fixture
async def analysis(tmp_path: Path) -> AsyncGenerator[ResearchAnalysis]:
    runtime = await setup(tmp_path, ControlledResearchModel())
    try:
        yield research_host(runtime).analysis
    finally:
        runtime.close()


async def test_selected_reads_keep_first_occurrence_order_and_active_scope(
    analysis: ResearchAnalysis, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = analysis.evidence("p")
    assert len(source) >= 2
    first, second = source[:2]
    calls: list[str] = []
    fixtures = {
        first.span_id: first,
        second.span_id: second,
        "foreign": first.model_copy(update={"project_id": "another-project"}),
        "inactive": first.model_copy(update={"artifact_id": "inactive-artifact"}),
    }

    def read(identifier: str) -> EvidenceSpan | None:
        calls.append(identifier)
        if identifier == "denied":
            raise ResourceScopeError("RESOURCE_ACCESS_DENIED")
        return fixtures.get(identifier)

    monkeypatch.setattr(analysis.artifacts, "read_evidence", read)
    selected = (
        second.span_id,
        "missing",
        "foreign",
        "denied",
        first.span_id,
        second.span_id,
        "inactive",
    )
    assert analysis.evidence_by_ids("p", iter(selected)) == (second, first)
    assert calls == list(dict.fromkeys(selected))
    bindings = analysis.governance.list_source_bindings("p")
    assert analysis.source_overview("p")

    def inactive(_project: str) -> tuple[SourceBinding, ...]:
        return tuple(binding.model_copy(update={"state": "INACTIVE"}) for binding in bindings)

    monkeypatch.setattr(analysis.governance, "list_source_bindings", inactive)
    assert analysis.evidence("p") == () and analysis.source_overview("p") == []
    assert analysis.evidence_by_ids("p", (first.span_id,)) == ()

    def unavailable(_identifier: str) -> EvidenceSpan | None:
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(analysis.artifacts, "read_evidence", unavailable)
    with pytest.raises(RuntimeError, match="storage unavailable"):
        analysis.evidence_by_ids("p", (first.span_id,))


@pytest.mark.parametrize(
    "mode", ("warm-hit", "warm-denied", "metadata-none", "eviction", "missing-document")
)
async def test_structure_cache_never_bypasses_metadata_scope_and_keeps_one_owner(
    analysis: ResearchAnalysis, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    span = analysis.evidence("p")[0]
    cache = cast(
        dict[tuple[str, str, str, str], tuple[dict[str, object] | None, str]],
        vars(analysis)["_structure_summaries"],
    )
    cache.clear()
    expected = analysis.source_structures((span,))
    assert expected[0]["nodes_digest"] and len(cache) == 1
    prior_cache = dict(cache)
    original_metadata = analysis.artifacts.read_structure_metadata_digest
    original_document = analysis.artifacts.read_structure
    calls: list[str] = []

    def metadata(project: str, artifact: str, version: str) -> str | None:
        calls.append("metadata")
        if mode == "warm-denied":
            raise ResourceScopeError("RESOURCE_ACCESS_DENIED")
        if mode == "metadata-none":
            return None
        return original_metadata(project, artifact, version)

    def document(project: str, artifact: str, version: str) -> StructuralDocument | None:
        calls.append("document")
        return None if mode == "missing-document" else original_document(project, artifact, version)

    monkeypatch.setattr(analysis.artifacts, "read_structure_metadata_digest", metadata)
    monkeypatch.setattr(analysis.artifacts, "read_structure", document)
    if mode == "warm-denied":
        with pytest.raises(ResourceScopeError, match="RESOURCE_ACCESS_DENIED"):
            analysis.source_structures((span,))
        assert calls == ["metadata"] and cache == prior_cache
    elif mode == "warm-hit":
        assert analysis.source_structures((span,)) == expected
        assert calls == ["metadata"] and cache == prior_cache
    else:
        if mode != "metadata-none":
            cache.clear()
        if mode == "eviction":
            cache.update({("p", "old", str(i), "old-metadata"): (None, "old") for i in range(128)})
        first = analysis.source_structures((span,))
        second = analysis.source_structures((span,))
        if mode == "missing-document":
            assert (
                first
                == second
                == [
                    {
                        "artifact_id": span.artifact_id,
                        "source_version_id": span.source_version_id,
                        "observation": None,
                        "nodes_digest": None,
                    }
                ]
            )
        else:
            assert first == second == expected
        if mode == "eviction":
            assert calls == ["metadata", "document", "metadata"] and len(cache) == 1
            assert all(key[1] != "old" for key in cache)
        else:
            assert calls == ["metadata", "document", "metadata", "document"]
            assert cache == (prior_cache if mode == "metadata-none" else {})
    assert vars(analysis)["_structure_summaries"] is cache


@pytest.mark.parametrize("include_structure", (False, True))
async def test_packet_reads_are_sorted_and_structure_callback_is_looked_up_last(
    analysis: ResearchAnalysis, monkeypatch: pytest.MonkeyPatch, include_structure: bool
) -> None:
    span = analysis.evidence("p")[0]
    artifact = analysis.artifacts.read_artifact(span.artifact_id)
    assert artifact is not None
    spans = (
        span.model_copy(update={"span_id": "z", "artifact_id": "b"}),
        span.model_copy(update={"span_id": "a", "artifact_id": "a"}),
        span.model_copy(update={"span_id": "m", "artifact_id": "missing"}),
    )
    calls: list[str] = []

    def early(_spans: tuple[EvidenceSpan, ...]) -> list[dict[str, object]]:
        pytest.fail("structure callback was captured before artifact reads")

    def late(values: tuple[EvidenceSpan, ...]) -> list[dict[str, object]]:
        assert values is spans
        calls.append("structures:late")
        return [{"source": "late"}]

    def read(identifier: str) -> ArtifactEnvelope | None:
        calls.append("artifact:" + identifier)
        monkeypatch.setattr(analysis, "source_structures", late)
        return (
            None
            if identifier == "missing"
            else artifact.model_copy(update={"artifact_id": identifier})
        )

    def corrections(project: str, identifier: str) -> tuple[SpanCorrectionRecord, ...]:
        assert project == "p"
        calls.append("correction:" + identifier)
        return ()

    monkeypatch.setattr(analysis, "source_structures", early)
    monkeypatch.setattr(analysis.artifacts, "read_artifact", read)
    monkeypatch.setattr(analysis.evidence_graph, "list_span_corrections", corrections)
    packet = analysis.source_packet(spans, include_structure=include_structure)
    assert (
        packet["originals_are_canonical"] is True
        and packet["source_text_is_untrusted_data"] is True
    )
    assert calls == [
        "artifact:a",
        "artifact:b",
        "artifact:missing",
        "correction:a",
        "correction:m",
        "correction:z",
    ] + (["structures:late"] if include_structure else [])
    assert ("structures" in packet) is include_structure
    if include_structure:
        assert packet["structures"] == [{"source": "late"}]


async def test_source_structure_keys_are_deduplicated_and_sorted(
    analysis: ResearchAnalysis, monkeypatch: pytest.MonkeyPatch
) -> None:
    span = analysis.evidence("p")[0]
    calls: list[tuple[str, str, str]] = []

    def summary(project: str, artifact: str, version: str) -> dict[str, object]:
        calls.append((project, artifact, version))
        return {"observation": None, "nodes_digest": "fixture"}

    monkeypatch.setattr(analysis, "_structure_summary", summary)
    keys = (("p", "b", "v2"), ("p", "a", "v2"), ("p", "b", "v2"), ("p", "a", "v1"))
    spans = tuple(
        span.model_copy(update={"project_id": p, "artifact_id": a, "source_version_id": v})
        for p, a, v in keys
    )
    result = analysis.source_structures(spans)
    assert calls == sorted(set(keys))
    assert [(item["artifact_id"], item["source_version_id"]) for item in result] == [
        (a, v) for _, a, v in sorted(set(keys))
    ]
