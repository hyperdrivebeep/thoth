"""G3 1b: the source digests and the journal of the original lane do not move when groups exist.

The digests below were taken from the code as it stood before structure groups were derived. A
group is extra context for new lanes; it is never part of the source basis or of a journaled
selection of the original lane.
"""

import hashlib
from datetime import UTC, datetime
from typing import cast

import pytest

from thoth.adapters.parsers.registry import default_parser_registry
from thoth.application.services import ingestion_service, research_selection_context
from thoth.application.services.behavior_context import BehaviorWorkContext, behavior_work_scope
from thoth.application.services.research_retrieval import lexical_candidates, work_candidates
from thoth.application.services.research_selection_context import candidate_context
from thoth.domain.artifact import ArtifactEnvelope, StructuralDocument
from thoth.domain.behavior_execution import ActiveBehaviorSnapshot
from thoth.domain.behavior_policy import RetrievalBehaviorPolicy
from thoth.domain.enums import (
    AuthorityState,
    CutoffState,
    SecurityClass,
    SupportState,
    VerificationState,
)
from thoth.domain.evaluation_run import sealed_payload
from thoth.domain.evidence import EvidenceSpan, connected_retrieval_spans
from thoth.domain.research_execution import ResearchBoundary, ResearchWork
from thoth.domain.research_request import RevisionRef
from thoth.ports.artifact_ledger import ArtifactLedgerPort

MARKDOWN = """# Benchmark report

Source: an opening report (excluded: later comments).

### What happens?

The sort slows down as keys are added.

Run it as:

```
=== v1 (4000 rows)
keys real median
1 0.001 0.001
2 0.001 0.001
=== v2 (4000 rows)
keys real median
1 0.006 0.006
2 0.011 0.011
```

### Performance

* v2
  * time per run: 75 mins
  * util: ~0-100%
* v1
  * time per run: 35 mins
  * util: ~85-100%

### Version

v2.0.0
"""
QUESTION = "v2 keys median time per run"
SOURCE_BASIS_DIGEST = "a9528ed1cb55c208a551f87d849604f0f67373fdb2f0da84d3015a775cb96c03"
ORDERED_REFS_DIGEST = "52267aedfb9b2a4f97019feb373f5f1a25daa1c916555058b2a4972a447e222c"
JOURNAL_INPUT_DIGEST = "642629fb8258a439d89b3752895ada160ef078e68fb2725deab823f1b59b4075"
JOURNAL_OUTPUT_DIGEST = "c91b527be9030e515b11b8318223c1a5a472af2af219e7287a90925a107296e6"
RETRIEVAL_POLICY_DIGEST = "26ca037c73bf5e6b15ec0a9d47bf642594ca159f9e6703ed8d23fdd7c9e1d40f"


class Reader:
    def __init__(self, doc: StructuralDocument, version: str) -> None:
        self.doc, self.version = doc, version

    def read_structure(self, project: str, artifact: str, version: str) -> StructuralDocument:
        assert version == self.version
        return self.doc


def source() -> tuple[tuple[EvidenceSpan, ...], Reader]:
    raw = MARKDOWN.encode()
    art = ArtifactEnvelope(
        artifact_id="artifact:digest-pin",
        project_id="project:g3",
        source_uri="report.md",
        media_type="text/markdown",
        byte_sha256=hashlib.sha256(raw).hexdigest(),
        authority=AuthorityState.INFORMAL,
        cutoff_state=CutoffState.ELIGIBLE,
        security_class=SecurityClass.INTERNAL,
        retrieved_at=datetime(2026, 10, 26, tzinfo=UTC),
        parser_name="unparsed",
        parser_version="0",
    )
    doc = default_parser_registry().parse(art, raw, source_path=None)
    version = "version:digest-pin"
    assert doc.context_observation is not None
    doc = doc.model_copy(update={"context_observation": doc.context_observation.bind(version)})
    kinds = cast(frozenset[object], vars(ingestion_service)["_EVIDENCE_NODE_KINDS"])
    nodes = [(n, n.text) for n in doc.nodes if n.kind in kinds and n.text]
    spans = tuple(
        EvidenceSpan(
            span_id=f"span:pin:{i:04d}",
            project_id="project:g3",
            artifact_id=doc.artifact.artifact_id,
            source_version_id=version,
            locator=n.locator.model_copy(update={"structural_node_id": n.node_id}),
            exact_text=text,
            text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            extraction_method="text:1.2.0",
            support_state=SupportState.EXTRACTED,
            authority_state=AuthorityState.INFORMAL,
            verification_state=VerificationState.SCHEMA_VALID,
            cutoff_state=CutoffState.ELIGIBLE,
        )
        for i, (n, text) in enumerate(nodes)
    )
    return spans, Reader(doc, version)


def snapshot() -> ActiveBehaviorSnapshot:
    return ActiveBehaviorSnapshot.model_validate(
        sealed_payload(
            "ACTIVE_BEHAVIOR_SNAPSHOT",
            "snapshot_digest",
            {
                "project_id": "p",
                "component": "RETRIEVAL_POLICY",
                "environment": "LOCAL",
                "artifact_ref": None,
                "content_digest": "a" * 64,
                "policy": RetrievalBehaviorPolicy(max_spans=12),
                "origin": "BASELINE",
                "registry_revision": 0,
                "exposure_ref": None,
                "ignored_registry_digest": None,
                "reason_code": None,
            },
        )
    )


def test_the_source_basis_and_the_ordered_refs_keep_their_digests() -> None:
    spans, reader = source()
    context = candidate_context(connected_retrieval_spans(spans), cast(ArtifactLedgerPort, reader))
    assert context.all_observed
    assert context.basis_digest == SOURCE_BASIS_DIGEST
    ordered = hashlib.sha256("|".join(context.ordered_refs).encode()).hexdigest()
    assert ordered == ORDERED_REFS_DIGEST and len(context.ordered_refs) == len(spans)


def test_the_original_lane_journals_the_same_digests() -> None:
    spans, reader = source()
    context = candidate_context(connected_retrieval_spans(spans), cast(ArtifactLedgerPort, reader))
    scope = BehaviorWorkContext("p", (snapshot(),))
    with behavior_work_scope(scope):
        lexical_candidates(QUESTION, (), spans, (), context=context)
    (use,) = scope.uses
    assert (use.input_digest, use.output_digest) == (JOURNAL_INPUT_DIGEST, JOURNAL_OUTPUT_DIGEST)


def test_the_recorded_selection_keeps_its_source_and_policy_digests() -> None:
    spans, reader = source()
    work = ResearchWork(
        RevisionRef(
            project_id="project:g3",
            entity_type="THREAD",
            entity_id="request:1",
            revision_id="revision:1",
            revision_digest="a" * 64,
        ),
        QUESTION,
        cast(ResearchBoundary, object()),
    )
    with behavior_work_scope(BehaviorWorkContext("p", (snapshot(),))):
        work_candidates(work, (), spans, cast(ArtifactLedgerPort, reader))
    assert work.retrieval_selection is not None
    assert work.retrieval_selection.source_basis_digest == SOURCE_BASIS_DIGEST
    assert work.retrieval_selection.policy_digest == RETRIEVAL_POLICY_DIGEST


def test_a_source_whose_groups_cannot_be_derived_is_ranked_without_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spans, reader = source()
    eligible = connected_retrieval_spans(spans)
    assert candidate_context(eligible, cast(ArtifactLedgerPort, reader)).groups

    def broken(*values: object) -> tuple[object, ...]:
        raise KeyError("region:missing")

    monkeypatch.setattr(research_selection_context, "derive_groups", broken)
    context = candidate_context(eligible, cast(ArtifactLedgerPort, reader))
    assert context.groups == ()
    assert context.all_observed and context.basis_digest == SOURCE_BASIS_DIGEST
    scope = BehaviorWorkContext("p", (snapshot(),))
    with behavior_work_scope(scope):
        lexical_candidates(QUESTION, (), spans, (), context=context)
    (use,) = scope.uses
    assert (use.input_digest, use.output_digest) == (JOURNAL_INPUT_DIGEST, JOURNAL_OUTPUT_DIGEST)
