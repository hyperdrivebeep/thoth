from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.integration.test_research_focus_basis import research_host
from tests.integration.test_research_memory_basis import Boundary
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

import thoth.application.services.research_analysis as analysis_module
from thoth.domain.artifact import StructuralDocument
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import CutoffState
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.evidence_bundle import ContextAssembly
from thoth.domain.evidence_graph import SpanCorrectionRecord
from thoth.domain.research_execution import ResearchWork
from thoth.domain.research_request import RevisionRef
from thoth.ports.artifact_ledger import ArtifactLedgerPort


def work() -> ResearchWork:
    return ResearchWork(
        RevisionRef(
            project_id="p",
            entity_type="THREAD",
            entity_id="request:cache",
            revision_id="request:cache",
            revision_digest="a" * 64,
        ),
        "latency",
        Boundary(),
    )


async def test_cache_tracks_its_actual_raw_structure_and_policy_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel())
    try:
        analysis = research_host(runtime).analysis
        source = analysis.evidence("p")
        candidates = source[:1]
        ranking = (source[0].span_id,)
        result = analysis.assemble(work(), ranking, candidates, source)
        calls: list[
            tuple[
                tuple[str, ...],
                tuple[EvidenceSpan, ...],
                tuple[EvidenceSpan, ...],
                ArtifactLedgerPort,
            ]
        ] = []

        def counted(
            ranking: tuple[str, ...],
            candidates: tuple[EvidenceSpan, ...],
            source: tuple[EvidenceSpan, ...],
            artifacts: ArtifactLedgerPort,
        ) -> ContextAssembly:
            calls.append((ranking, candidates, source, artifacts))
            return result

        monkeypatch.setattr(analysis_module, "assemble_context", counted)
        mutations = [
            {"exact_text": source[0].exact_text + " changed"},
            {"text_sha256": "f" * 64},
            {"locator": source[0].locator.model_copy(update={"page": 99})},
            {"source_version_id": "version:other"},
            {"cutoff_state": CutoffState.UNKNOWN_TIME},
        ]
        for change in mutations:
            current = work()
            analysis.assemble(current, ranking, candidates, source)
            baseline = len(calls)
            cached = analysis.assemble(current, ranking, candidates, source)
            assert len(calls) == baseline and cached.wave.cache_hit
            changed = (source[0].model_copy(update=change), *source[1:])
            analysis.assemble(current, ranking, candidates, changed)
            assert len(calls) == baseline + 1
        current = work()
        analysis.assemble(current, ranking, candidates, source)
        baseline = len(calls)
        original_structure = analysis.artifacts.read_structure

        def changed_structure(
            project_id: str, artifact_id: str, source_version_id: str
        ) -> StructuralDocument | None:
            document = original_structure(project_id, artifact_id, source_version_id)
            if document is None or not document.nodes:
                return document
            return document.model_copy(
                update={
                    "nodes": (
                        document.nodes[0].model_copy(update={"text": "changed structure"}),
                        *document.nodes[1:],
                    )
                }
            )

        with monkeypatch.context() as patch:
            patch.setattr(analysis.artifacts, "read_structure", changed_structure)
            analysis.assemble(current, ranking, candidates, source)
            assert len(calls) == baseline + 1
        current = work()
        analysis.assemble(current, ranking, candidates, source)
        baseline = len(calls)
        policy = analysis_module.retrieval_policy()
        with monkeypatch.context() as patch:
            patch.setattr(
                analysis_module,
                "retrieval_policy",
                lambda: policy.model_copy(update={"character_budget": policy.character_budget - 1}),
            )
            analysis.assemble(current, ranking, candidates, source)
            assert len(calls) == baseline + 1
    finally:
        runtime.close()


async def test_selected_correction_changes_packet_digest_without_polluting_assembly_key(
    tmp_path: Path,
) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel())
    try:
        analysis = research_host(runtime).analysis
        source = analysis.evidence("p")
        chosen = source[:1]
        current = work()
        first = analysis.assemble(current, (chosen[0].span_id,), chosen, source)
        before = analysis.source_digest(first.evidence)
        correction = SpanCorrectionRecord(
            correction_id="correction:cache",
            project_id="p",
            span_id=chosen[0].span_id,
            corrected_text="explicit corrected interpretation",
            reason="controlled correction",
            correction_digest="a" * 64,
            created_at=datetime(2026, 9, 22, tzinfo=UTC),
        )
        analysis.evidence_graph.add_span_correction(correction)
        cached = analysis.assemble(current, (chosen[0].span_id,), chosen, source)
        assert cached.wave.cache_hit
        packet = analysis.source_packet(cached.evidence)
        assert packet["corrections"] == [correction.model_dump(mode="json")]
        assert analysis.source_digest(cached.evidence) != before
        # The old 2.0 packet still omits structure; adding the helper changes no wire fields.
        legacy = {key: child for key, child in packet.items() if key != "structures"}
        assert analysis.source_digest(cached.evidence, version="2.0.0") == domain_digest(
            "SOURCE_CONTEXT", "2.0.0", canonical_payload(legacy)
        )
        assert analysis.source_digest(cached.evidence) == domain_digest(
            "SOURCE_CONTEXT", "2.1.0", canonical_payload(packet)
        )
    finally:
        runtime.close()
