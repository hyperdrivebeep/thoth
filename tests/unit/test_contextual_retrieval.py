"""New text observations are consumed; legacy formats and permission filters stay intact."""

import json
from dataclasses import replace
from typing import cast

import pytest
from tests.unit.test_text_context_observation import bound, parse_text, spans

from thoth.adapters.models.codex_oauth import strict_output_schema
from thoth.adapters.models.context_projection import compact_model_context
from thoth.adapters.models.reference_schema import constrain_span_references
from thoth.application.services import research_retrieval
from thoth.application.services.research_assembly import assemble_research_context
from thoth.application.services.research_context_assembler import assemble_context
from thoth.application.services.research_retrieval import lexical_candidates, work_candidates
from thoth.application.services.research_role_context import bundle_view, role_context
from thoth.application.services.research_selection_context import candidate_context
from thoth.application.services.research_source_digest import versioned_source_digest
from thoth.application.services.retrieval_coverage_view import with_retrieval_coverage
from thoth.domain.artifact import StructuralDocument
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import CutoffState, ModelRole
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.evidence_requirements import EvidenceRanking
from thoth.domain.model import ContextPack
from thoth.domain.research_execution import ResearchBoundary, ResearchWork
from thoth.domain.research_request import RevisionRef
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.model import ModelExecutionHold

TEXT = (
    "# Benchmark\n\nRelease alpha\n\n```text\ntime cols=100 value=0.005 seconds\n```\n\n"
    "Release beta\n\n```text\ntime cols=100 value=1.557 seconds\n```\n"
)


def test_reranker_may_read_context_but_only_rank_admitted_candidates() -> None:
    schema = constrain_span_references(
        strict_output_schema(EvidenceRanking),
        ("anchor", "context"),
        {"ranking_candidate_refs": ("anchor",)},
    )
    value = json.loads(json.dumps(schema))
    assert value["properties"]["ordered_span_ids"]["items"]["enum"] == ["anchor"]
    with pytest.raises(ValueError, match="RANKING_CANDIDATE_SCOPE_INVALID"):
        constrain_span_references(
            strict_output_schema(EvidenceRanking),
            ("anchor",),
            {"ranking_candidate_refs": ("outside",)},
        )


class Reader:
    def __init__(self, document: StructuralDocument):
        self.document = document

    def read_structure(self, project: str, artifact: str, version: str) -> StructuralDocument:
        assert (
            project == self.document.artifact.project_id
            and artifact == self.document.artifact.artifact_id
        )
        return self.document


def work() -> ResearchWork:
    return ResearchWork(
        RevisionRef(
            project_id="project:context",
            entity_type="THREAD",
            entity_id="request:1",
            revision_id="revision:1",
            revision_digest="a" * 64,
        ),
        "time seconds",
        cast(ResearchBoundary, object()),
    )


def context(source: tuple[EvidenceSpan, ...] = ()) -> ContextPack:
    return ContextPack(
        case_id="c",
        project_id="project:context",
        object_id="o",
        problem="time seconds",
        evidence=source,
        criteria=(),
        sufficiency=None,
        input_head_set_digest="0" * 64,
    )


def test_complete_small_catalog_and_exact_envelope() -> None:
    doc = bound(parse_text(TEXT))
    source = spans(doc)
    reader = cast(ArtifactLedgerPort, Reader(doc))
    observed = candidate_context(source, reader)
    candidates = lexical_candidates("seconds", (), source, context=observed)
    assert candidates == source
    anchor = next(s for s in source if "1.557" in s.exact_text)
    assembly = assemble_context((anchor.span_id,), candidates, source, reader)
    texts = {s.exact_text for s in assembly.evidence}
    assert {"# Benchmark", "Release beta", anchor.exact_text} <= texts
    assert "Release alpha" not in texts
    assert assembly.bundles[0].schema_version == "2.0.0"
    assert assembly.bundles[0].context_regions


@pytest.mark.parametrize("corruption", ["missing", "extra", "duplicate"])
def test_inconsistent_context_order_falls_back_with_model_limitation(
    corruption: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = bound(parse_text(TEXT))
    source = spans(doc)
    reader = cast(ArtifactLedgerPort, Reader(doc))
    observed = candidate_context(source, reader)
    refs = observed.ordered_refs
    broken_refs = (
        refs[:-1]
        if corruption == "missing"
        else (*refs, "span:not-eligible" if corruption == "extra" else refs[0])
    )
    broken = replace(observed, ordered_refs=broken_refs)
    monkeypatch.setattr(research_retrieval, "candidate_context", lambda *_args: broken)
    w = work()
    selected = work_candidates(w, (), source, reader)
    assert selected == lexical_candidates(w.effective_question, (), source, context=broken)
    assert selected == lexical_candidates(w.effective_question, (), source)
    assert {s.span_id for s in selected} <= {s.span_id for s in source}
    assert w.retrieval_selection is not None and w.retrieval_selection.mode == "LEXICAL"
    received = with_retrieval_coverage(context(selected), ModelRole.RESEARCH_PLANNER, w)
    coverage = received.research_context["retrieval_coverage"]
    assert isinstance(coverage, dict)
    assert "CONTEXT_ORDERED_REFS_MISMATCH" in coverage["limitations"]


def test_legacy_source_and_denied_span_do_not_gain_complete_mode() -> None:
    doc = bound(parse_text(TEXT))
    source = spans(doc)
    denied = source[-1].model_copy(update={"cutoff_state": CutoffState.PROHIBITED_CONTEXT})
    reader = cast(ArtifactLedgerPort, Reader(doc))
    eligible = source[:-1]
    selected = work_candidates(work(), (), (*eligible, denied), reader)
    assert denied.span_id not in {s.span_id for s in selected}
    legacy = doc.model_copy(update={"context_observation": None})
    legacy_context = candidate_context(source, cast(ArtifactLedgerPort, Reader(legacy)))
    assert not legacy_context.all_observed
    assert lexical_candidates("seconds", (), source, context=legacy_context) == lexical_candidates(
        "seconds", (), source
    )


@pytest.mark.parametrize(
    "role",
    [
        ModelRole.RESEARCH_PLANNER,
        ModelRole.EVIDENCE_RERANKER,
        ModelRole.SEMANTIC_REVIEWER,
        ModelRole.REVIEW_ADJUDICATOR,
        ModelRole.SOURCE_PLANNER,
        ModelRole.HYPOTHESIS_GENERATOR,
        ModelRole.HYPOTHESIS_REVIEWER,
        ModelRole.ACTION_PLANNER,
    ],
)
def test_coverage_survives_every_role_projection(role: ModelRole) -> None:
    doc = bound(parse_text(TEXT))
    source = spans(doc)
    w = work()
    work_candidates(w, (), source, cast(ArtifactLedgerPort, Reader(doc)))
    received = role_context(with_retrieval_coverage(context(source), role, w), role)
    coverage = received.research_context["retrieval_coverage"]
    assert isinstance(coverage, dict)
    assert coverage["catalog_count"] == len(source) == coverage["input_span_count"]
    assert coverage["all_eligible_in_candidates"] is True
    assert coverage["scope"] == "AUTHORIZED_CONNECTED_EXTRACTED_SOURCE"
    assert received.research_context["context_projection_version"] == "2.0.0"


def test_scope_corruption_and_legacy_digest_are_not_silently_repaired() -> None:
    doc = bound(parse_text(TEXT))
    source = spans(doc)
    assert doc.context_observation is not None
    invalid = doc.model_copy(
        update={
            "context_observation": doc.context_observation.model_copy(
                update={"source_version_id": "other"}
            )
        }
    )
    with pytest.raises(ValueError, match="CONTEXT_OBSERVATION_VERSION_MISMATCH"):
        candidate_context(source, cast(ArtifactLedgerPort, Reader(invalid)))
    bad_span = source[0].model_copy(update={"exact_text": "changed"})
    with pytest.raises(ValueError, match="CONTEXT_SOURCE_MEMBER_INVALID"):
        candidate_context((bad_span, *source[1:]), cast(ArtifactLedgerPort, Reader(doc)))
    packet = {
        "artifacts": [],
        "structures": [{"nodes_digest": "a", "context_observation_digest": "b"}],
    }
    legacy = {"artifacts": [], "structures": [{"nodes_digest": "a"}]}
    assert versioned_source_digest(packet, "2.1.0") == domain_digest(
        "SOURCE_CONTEXT", "2.1.0", canonical_payload(legacy)
    )
    assert versioned_source_digest(packet, "2.2.0") != versioned_source_digest(packet, "2.1.0")


def test_large_catalog_retains_short_structural_heading_with_unchanged_limits() -> None:
    doc = bound(
        parse_text(
            "# 탐지율\n\n"
            + "\n\n".join(f"unrelated long observation number {i}" for i in range(170))
        )
    )
    source = spans(doc)
    w = work()
    w.effective_question = "탐지율"
    candidates = work_candidates(w, (), source, cast(ArtifactLedgerPort, Reader(doc)))
    assert len(source) == 171
    assert source[0] in candidates and len(source[0].exact_text) < 24
    assert len(candidates) <= 160 and sum(len(s.exact_text) for s in candidates) <= 50000
    assert w.retrieval_selection is not None and w.retrieval_selection.mode == "LEXICAL"
    assert source[0] not in lexical_candidates("탐지율", (), source)


def test_mixed_catalog_uses_legacy_selection_with_supported_text_context() -> None:
    observed = bound(parse_text(TEXT))
    legacy = bound(
        parse_text("short\n\nlegacy long seconds observation\n", artifact_id="artifact:legacy")
    )
    legacy = legacy.model_copy(update={"context_observation": None})

    class MixedReader:
        def read_structure(self, project: str, artifact: str, version: str) -> StructuralDocument:
            return observed if artifact == observed.artifact.artifact_id else legacy

    reader = cast(ArtifactLedgerPort, MixedReader())
    source = (*spans(observed), *spans(legacy))
    w = work()
    candidates = work_candidates(w, (), source, reader)
    assert w.retrieval_selection is not None
    assert w.retrieval_selection.mode == "LEGACY"
    assert "LEGACY_CONTEXT" in w.retrieval_selection.limitations
    assert not candidate_context(source, reader).all_observed
    assert len(candidates) < len(source)
    anchor = next(s for s in candidates if "1.557" in s.exact_text)
    assembly = assemble_context((anchor.span_id,), candidates, source, reader)
    assert "Release beta" in {s.exact_text for s in assembly.evidence}


def test_assembly_cache_refreshes_selection_coverage() -> None:
    doc = bound(parse_text(TEXT))
    source = spans(doc)
    reader = cast(ArtifactLedgerPort, Reader(doc))

    class Boundary:
        def check(self) -> None:
            pass

    w = work()
    w.boundary = cast(ResearchBoundary, Boundary())
    candidates = work_candidates(w, (), source, reader)
    ranking = (next(s.span_id for s in candidates if "1.557" in s.exact_text),)
    first = assemble_research_context(w, ranking, candidates, source, reader, lambda _: [])
    work_candidates(w, (), source, reader)
    second = assemble_research_context(w, ranking, candidates, source, reader, lambda _: [])
    assert second.wave.cache_hit and second.evidence == first.evidence
    received = with_retrieval_coverage(context(second.evidence), ModelRole.SEMANTIC_REVIEWER, w)
    coverage = received.research_context["retrieval_coverage"]
    assert isinstance(coverage, dict)
    assert coverage["ranked_anchor_count"] == 1
    assert coverage["input_span_refs"] == [s.span_id for s in second.evidence]
    assert coverage["context_added_count"] == len(second.evidence) - 1


def test_pinned_anchor_cannot_hide_unadmitted_mandatory_context() -> None:
    doc = bound(
        parse_text(
            "# Heading\n\n" + "x" * 50001 + "\n\n```\ntime value=0.5 seconds observed\n```\n"
        )
    )
    source = spans(doc)

    class Boundary:
        def check(self) -> None:
            pass

    w = work()
    w.boundary = cast(ResearchBoundary, Boundary())
    anchor = next(s for s in source if "value=0.5" in s.exact_text)
    w.pinned_spans = (anchor.span_id,)
    reader = cast(ArtifactLedgerPort, Reader(doc))
    candidates = work_candidates(w, (), source, reader)
    with pytest.raises(ModelExecutionHold, match="PINNED_CONTEXT_NOT_ADMITTED"):
        assemble_research_context(
            w, (anchor.span_id,), candidates, source, reader, lambda _source: []
        )


def test_compact_projection_keeps_quote_and_source_identity_without_mutating_basis() -> None:
    doc = bound(parse_text(TEXT))
    source = spans(doc)
    w = work()
    reader = cast(ArtifactLedgerPort, Reader(doc))
    candidates = work_candidates(w, (), source, reader)
    assembly = assemble_context(tuple(s.span_id for s in candidates), candidates, source, reader)
    original = with_retrieval_coverage(context(source), ModelRole.SEMANTIC_REVIEWER, w)
    original = original.model_copy(
        update={
            "research_context": {
                **original.research_context,
                "context_bundle": bundle_view(assembly),
            }
        }
    )
    before = canonical_payload(original)
    project, evidence = map(json.loads, compact_model_context(original))
    assert canonical_payload(original) == before
    assert len(evidence["sources"]) == 1 and len(evidence["documents"]) == 1
    assert [(x["span_id"], x["exact_text"], x["locator"]) for x in evidence["spans"]] == [
        (s.span_id, s.exact_text, s.locator.model_dump(mode="json", exclude_none=True))
        for s in source
    ]
    assert evidence["documents"][0]["source_version_id"] == "version:context"
    assert project["research_context"]["retrieval_coverage"]["input_span_count"] == len(source)
    assert all("text_sha256" not in s for s in evidence["spans"])
    full_bundle = json.loads(canonical_payload(bundle_view(assembly)))
    compact_bundle = project["research_context"]["context_bundle"]
    for old, new in zip(full_bundle["bundles"], compact_bundle["bundles"], strict=True):
        assert [evidence["spans"][index]["span_id"] for index in new["quote_refs"]] == list(
            old["span_refs"]
        )
        assert [evidence["spans"][index]["span_id"] for index in new["anchor_quote_refs"]] == list(
            old["anchor_span_refs"]
        )


def test_unpinned_required_context_omission_survives_cache_and_reaches_model() -> None:
    doc = bound(
        parse_text(
            "# Heading\n\n" + "x" * 50001 + "\n\n```\ntime value=0.5 seconds observed\n```\n"
        )
    )
    source = spans(doc)
    reader = cast(ArtifactLedgerPort, Reader(doc))
    anchor = next(s for s in source if "value=0.5" in s.exact_text)

    class Boundary:
        def check(self) -> None:
            pass

    w = work()
    w.boundary = cast(ResearchBoundary, Boundary())
    for cached in (False, True):
        candidates = work_candidates(w, (), source, reader)
        assembly = assemble_research_context(
            w, (anchor.span_id,), candidates, source, reader, lambda _: []
        )
        assert assembly.wave.cache_hit is cached and not assembly.evidence
        assert assembly.wave.termination == "POLICY_LIMIT"
        received = with_retrieval_coverage(
            context(assembly.evidence), ModelRole.SEMANTIC_REVIEWER, w
        )
        coverage = received.research_context["retrieval_coverage"]
        assert isinstance(coverage, dict)
        assert coverage["omitted_anchor_refs"] == [anchor.span_id]
        assert {"POLICY_LIMIT", "REQUIRED_CONTEXT_NOT_ADMITTED"} <= set(coverage["limitations"])
        assert coverage["input_span_count"] == 0 and coverage["coverage_state"] == "PARTIAL"
