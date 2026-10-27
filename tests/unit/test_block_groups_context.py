"""G3 1c: BLOCK groups of a source with no observation reach the lanes through candidate_context.

G3 1b tested BLOCK groups only on synthetic input. Here the whole link is run: spans with parent
pointers and no structure observation, the candidate context built from them, and the lane that
reads the groups.
"""

from typing import cast

from tests.unit.group_fixtures import Reader, context, parse
from tests.unit.test_ranked_lanes import policy
from tests.unit.test_retrieval_selection_oracle import span
from tests.unit.test_retrieval_source_digests import MARKDOWN

from thoth.application.services.research_selection_context import candidate_context
from thoth.domain.artifact import StructuralDocument
from thoth.domain.evidence import EvidenceSpan, connected_retrieval_spans
from thoth.domain.retrieval_lane import (
    GROUPED_BM25,
    CatalogContext,
    RetrievalRequest,
    select_retrieval_context,
)
from thoth.ports.artifact_ledger import ArtifactLedgerPort

POINTERS = {
    "host": ("/db/host", "database host name is alpha.example.org"),
    "port": ("/db/port", "database port number is 5432"),
    "user": ("/db/user", "database user account is readonly"),
    "ttl": ("/cache/ttl", "cache time to live is 300 seconds"),
    "size": ("/cache/size", "cache size limit is 128 megabytes"),
    "name": ("/name", "service name is billing"),
}


def config_spans() -> tuple[EvidenceSpan, ...]:
    return tuple(
        span(name, text, artifact="artifact:cfg", line=index, pointer=pointer).model_copy(
            update={"project_id": "project:g3"}
        )
        for index, (name, (pointer, text)) in enumerate(POINTERS.items(), start=1)
    )


class NoStructure:
    """The ledger holds no structure for the config source; the Markdown source has its own."""

    def __init__(self) -> None:
        self.docs: dict[tuple[str, str], StructuralDocument] = {}

    def read_structure(
        self, project: str, artifact: str, version: str
    ) -> StructuralDocument | None:
        return self.docs.get((artifact, version))


def ids(refs: tuple[str, ...]) -> list[str]:
    return [ref.removeprefix("span:") for ref in refs]


def test_a_source_with_no_observation_gets_block_groups_from_its_pointers() -> None:
    spans = config_spans()
    reader = NoStructure()
    ctx = candidate_context(connected_retrieval_spans(spans), cast(ArtifactLedgerPort, reader))
    assert not ctx.all_observed and ctx.limitations == ("LEGACY_CONTEXT",)
    assert [(g.kind, ids(g.body_refs)) for g in ctx.groups] == [
        ("BLOCK", ["host", "port", "user"]),
        ("BLOCK", ["ttl", "size"]),
    ]
    assert all(g.title_refs == () for g in ctx.groups)


def test_the_lane_reads_those_groups_and_returns_the_block_in_document_order() -> None:
    spans = config_spans()
    ctx = candidate_context(
        connected_retrieval_spans(spans), cast(ArtifactLedgerPort, NoStructure())
    )
    request = RetrievalRequest("database port", (), spans, (), ctx.to_catalog())
    grouped = select_retrieval_context(request, policy(GROUPED_BM25, max_spans=3))
    assert ids(tuple(s.span_id for s in grouped.selected)) == ["host", "port", "user"]
    assert grouped.mode == "LEGACY"
    # Without the groups the same lane ranks single spans, and the best one comes first.
    flat = RetrievalRequest(
        "database port", (), spans, (), CatalogContext((), frozenset(), False, 0)
    )
    single = select_retrieval_context(flat, policy(GROUPED_BM25, max_spans=3))
    assert ids(tuple(s.span_id for s in single.selected))[0] == "port"


def test_the_groups_of_an_observed_and_an_unobserved_source_never_mix() -> None:
    reader = NoStructure()
    report = parse(MARKDOWN, "rep", cast(Reader, reader))
    spans = (*report, *config_spans())
    ctx = context(spans, cast(Reader, reader))
    artifact = {s.span_id: s.artifact_id for s in spans}
    assert len(ctx.groups) > 2
    for group in ctx.groups:
        assert len({artifact[ref] for ref in (*group.title_refs, *group.body_refs)}) == 1
    kinds = {artifact[g.body_refs[0]]: g.kind for g in ctx.groups if g.kind == "BLOCK"}
    assert kinds == {"artifact:cfg": "BLOCK"}
    # Each span of the observed source is still in exactly one body.
    bodies = [ref for g in ctx.groups for ref in g.body_refs if artifact[ref] == "artifact:rep"]
    assert len(bodies) == len(set(bodies)) == len(report)
