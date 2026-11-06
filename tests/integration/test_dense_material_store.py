"""Dense material over the real index store: stored windows, the stamp and the service hooks."""

from pathlib import Path
from typing import Any

import pytest
from tests.embedding_fixtures import PROJECT, IndexFixture, fixture, unit_vector

from thoth.application.services.dense_material import DenseMaterialProvider
from thoth.domain.embedding_index import EmbeddingVector, max_window_score
from thoth.domain.evidence import EvidenceSpan

pytestmark = pytest.mark.local_retrieval
pytest.importorskip("numpy", reason="numpy 없음(local-retrieval extra)")

LONG = 1330  # tokens: four windows


def provider_of(x: IndexFixture) -> DenseMaterialProvider:
    return DenseMaterialProvider(
        stamp=x.stores.embedding_index.index_stamp, index=lambda: x.service
    )


async def index(x: IndexFixture, *names: str) -> tuple[EvidenceSpan, ...]:
    spans = tuple(x.ingest(name) for name in names)
    x.service.reconcile(PROJECT)
    assert all(job.state == "READY" for job in await x.service.drain(PROJECT))
    return spans


async def test_scores_over_stored_windows_equal_the_reference_read_path(tmp_path: Path) -> None:
    x = fixture(tmp_path)
    try:
        long = x.ingest("long")
        x.worker.sizes[long.exact_text] = LONG
        x.service.reconcile(PROJECT)
        await x.service.drain(PROJECT)
        spans = (long, *await index(x, "b", "c"))
        provider = provider_of(x)
        material = await provider.material(PROJECT, "which lantern", spans)
        assert material is not None and material.unavailable_reason is None
        windows = x.service.read_window_vectors(PROJECT, spans)
        assert [len(w) for w in windows] == [4, 1, 1]
        query = EmbeddingVector.from_values(unit_vector("which lantern"))
        assert {s.span_id: s.score for s in material.scores} == {
            span.span_id: max_window_score(query, w) for span, w in zip(spans, windows, strict=True)
        }
    finally:
        x.stores.close()


async def test_the_stamp_and_the_hooks_follow_the_stored_index(tmp_path: Path) -> None:
    x = fixture(tmp_path)
    try:
        store: Any = x.stores.embedding_index
        assert store.index_stamp(PROJECT) == (True, 1, ())
        spans = await index(x, "a", "b")
        provider = provider_of(x)
        stamp = store.index_stamp(PROJECT)
        assert stamp[0] and len(stamp[2]) == 2
        assert await provider.material(PROJECT, "q", spans) is not None
        assert provider.resident_projects() == (PROJECT,)
        # A source that is not indexed yet: the stamp is unchanged and dense falls back.
        extra = x.ingest("c")
        pending = await provider.material(PROJECT, "q", (*spans, extra))
        assert pending is not None and pending.unavailable_reason == "INDEX_INCOMPLETE"
        # Indexing it moves the stamp (a new READY job) and the service tells the provider.
        x.service.reconcile(PROJECT)
        assert provider.resident_projects() == ()
        await x.service.drain(PROJECT)
        assert store.index_stamp(PROJECT) != stamp
        assert await provider.material(PROJECT, "q", (*spans, extra)) is not None
        # A source that leaves READY changes the stamp too.
        before = store.index_stamp(PROJECT)
        store.invalidate(PROJECT, extra.artifact_id)
        assert store.index_stamp(PROJECT) != before
        stale = await provider.material(PROJECT, "q", (*spans, extra))
        assert stale is not None and stale.unavailable_reason == "INDEX_INCOMPLETE"
        # Switched off through the service: no material and no resident copy.
        x.service.configure(
            PROJECT, False, x.stores.embedding_index.configuration(PROJECT).generation
        )
        assert provider.resident_projects() == ()
        assert store.index_stamp(PROJECT)[0] is False
        assert await provider.material(PROJECT, "q", spans) is None
    finally:
        x.stores.close()
