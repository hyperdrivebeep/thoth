"""The resident window matrix: scores equal the reference, and the copy follows the index."""

import math
import random
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, cast

import pytest
from tests.unit.test_retrieval_routing import span

from thoth.application.services.dense_material import (
    WINDOW_BYTES,
    DenseMaterialProvider,
    IndexStamp,
    Resident,
)
from thoth.application.services.embedding_index_service import EmbeddingIndexService
from thoth.domain.embedding_index import EmbeddingIndexError, EmbeddingVector, max_window_score
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.retrieval_embedding import DenseMaterial, DenseUnavailableReason
from thoth.domain.retrieval_lane import RetrievalRequest, select_retrieval_context
from thoth.domain.retrieval_routing_policy import RetrievalRoutingPolicy

pytestmark = pytest.mark.local_retrieval
np: Any = pytest.importorskip("numpy", reason="numpy 없음(local-retrieval extra)")


def unit(rng: random.Random) -> tuple[float, ...]:
    raw = [rng.gauss(0, 1) for _ in range(384)]
    norm = math.sqrt(sum(x * x for x in raw))
    return tuple(x / norm for x in raw)


class QueryWorker:
    def __init__(self, vector: tuple[float, ...]) -> None:
        self.vector, self.prepared = vector, True
        self.error: Exception | None = None
        self.asked: list[str] = []

    async def encode_one(self, text: str, *, role: Literal["QUERY", "PASSAGE"]) -> EmbeddingVector:
        assert role == "QUERY"
        self.asked.append(text)
        if self.error is not None:
            raise self.error
        return EmbeddingVector.from_values(self.vector)


@dataclass
class Index:
    """The two methods of EmbeddingIndexService the provider reads, over synthetic windows."""

    worker: QueryWorker
    sources: dict[tuple[str, str], list[tuple[str, str, tuple[EmbeddingVector, ...]]]]
    reads: int = 0
    fail: Exception | None = None
    on_change: Any = None
    spans: dict[str, EvidenceSpan] = field(default_factory=dict)

    def ready_sources(self, project: str) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self.sources))

    def read_source_windows(
        self, project: str, artifact: str, version: str
    ) -> tuple[tuple[str, str, tuple[EmbeddingVector, ...]], ...]:
        self.reads += 1
        if self.fail is not None:
            raise self.fail
        return tuple(self.sources[(artifact, version)])


def build(
    seed: int = 1, artifacts: int = 6, per: int = 12, max_windows: int = 4
) -> tuple[Index, tuple[EvidenceSpan, ...], tuple[float, ...]]:
    rng = random.Random(seed)
    sources: dict[tuple[str, str], list[tuple[str, str, tuple[EmbeddingVector, ...]]]] = {}
    spans: list[EvidenceSpan] = []
    for a in range(artifacts):
        rows: list[tuple[str, str, tuple[EmbeddingVector, ...]]] = []
        for j in range(per):
            item = span(a * per + j, f"a{a}", f"passage {a}/{j} of the synthetic corpus")
            windows = tuple(
                EmbeddingVector.from_values(unit(rng)) for _ in range(rng.randint(1, max_windows))
            )
            rows.append((item.span_id, item.text_sha256, windows))
            spans.append(item)
        sources[(f"a{a}", f"v:a{a}")] = rows
    query = unit(rng)
    return Index(QueryWorker(query), sources), tuple(spans), query


def make(
    index: Index,
    stamp: list[IndexStamp] | None = None,
    **options: Any,
) -> DenseMaterialProvider:
    current = stamp if stamp is not None else [(True, 1, (("job:1", 1),))]
    return DenseMaterialProvider(
        stamp=lambda _: current[0], index=lambda: cast(EmbeddingIndexService, index), **options
    )


def scores(material: DenseMaterial | None) -> dict[str, Decimal]:
    assert material is not None and material.unavailable_reason is None
    return {s.span_id: s.score for s in material.scores}


@pytest.mark.parametrize("seed", [1, 2, 3])
async def test_resident_scores_equal_the_reference_for_every_passage(seed: int) -> None:
    index, spans, query = build(seed, artifacts=8, per=40)
    got = scores(await make(index).material("p", "lantern", spans))
    by_id = {r[0]: r[2] for rows in index.sources.values() for r in rows}
    reference = {
        span_id: max_window_score(EmbeddingVector.from_values(query), windows)
        for span_id, windows in by_id.items()
    }
    assert got == {s.span_id: reference[s.span_id] for s in spans}
    assert {len(w) for w in by_id.values()} > {1}  # passages with several windows were scored


@pytest.mark.parametrize("sign", [1e-13, -1e-13])
def test_a_value_near_a_rounding_boundary_or_zero_is_computed_the_reference_way(
    sign: float,
) -> None:
    class Noisy(np.ndarray):
        def __matmul__(self, other: Any) -> Any:
            return np.asarray(self).__matmul__(other) + sign

    rng = random.Random(5)
    boundary = None
    while boundary is None:
        candidate = float(np.float32(rng.uniform(0.1, 0.9)))
        distance = abs((candidate * 1e9) % 1 - 0.5)
        if distance < 2e-5:
            boundary = candidate
    query = np.zeros(384)
    query[0] = 1.0
    rows = np.zeros((2, 384))
    rows[0, 0], rows[1, 0] = boundary, 0.0
    resident = Resident(
        (True, 1, ()),
        rows.view(Noisy),
        np.array([0, 1]),
        np.array([1, 2]),
        {"x": 0, "y": 1},
        ["", ""],
    )
    values = DenseMaterialProvider.best_scores(np, resident, query, [0, 1])
    assert values == [boundary, 0.0]
    assert f"{values[1]:.9f}" == "0.000000000"  # not "-0.000000000" whichever way noise went


async def test_the_resident_copy_is_read_once_and_reused_while_the_index_is_unchanged() -> None:
    index, spans, _ = build()
    provider = make(index)
    first = await provider.material("p", "lantern", spans)
    reads = index.reads
    assert reads == len(index.sources) and provider.resident_projects() == ("p",)
    again = await provider.material("p", "another question", spans)
    assert index.reads == reads and again is not None and again != first
    assert index.worker.asked == ["lantern", "another question"]
    assert (
        provider.resident_bytes()
        == sum(len(w) for rows in index.sources.values() for _, _, w in rows) * WINDOW_BYTES
    )


async def test_a_new_index_state_replaces_the_copy_and_a_switch_off_drops_it() -> None:
    index, spans, _ = build()
    stamp: list[IndexStamp] = [(True, 1, (("job:1", 1),))]
    provider = make(index, stamp)
    await provider.material("p", "lantern", spans)
    assert index.reads == len(index.sources)
    # A source indexed again (a new READY job): the copy is read again.
    stamp[0] = (True, 1, (("job:1", 1), ("job:2", 1)))
    await provider.material("p", "lantern", spans)
    assert index.reads == 2 * len(index.sources)
    # A source that left READY (STALE): its generation moves and the copy is read again.
    stamp[0] = (True, 1, (("job:1", 2),))
    await provider.material("p", "lantern", spans)
    assert index.reads == 3 * len(index.sources)
    # Index change events drop the copy at once.
    provider.invalidate("p")
    assert provider.resident_projects() == () and provider.resident_bytes() == 0
    await provider.material("p", "lantern", spans)
    assert provider.resident_projects() == ("p",)
    # Switched off: no material, and nothing stays in memory.
    stamp[0] = (False, 2, ())
    assert await provider.material("p", "lantern", spans) is None
    assert provider.resident_projects() == ()


async def test_the_service_hook_is_set_so_index_changes_drop_the_copy() -> None:
    index, spans, _ = build()
    provider = make(index)
    await provider.material("p", "lantern", spans)
    assert index.on_change == provider.invalidate
    index.on_change("p")
    assert provider.resident_projects() == ()


async def test_a_source_the_index_does_not_have_or_whose_text_changed_falls_back() -> None:
    index, spans, _ = build(artifacts=3, per=4)
    extra = span(999, "a9", "a source added after the index was made")
    provider = make(index)
    missing = await provider.material("p", "lantern", (*spans, extra))
    assert missing is not None and missing.unavailable_reason == "INDEX_INCOMPLETE"
    edited = (*spans[:-1], spans[-1].model_copy(update={"text_sha256": "f" * 64}))
    stale = await provider.material("p", "lantern", edited)
    assert stale is not None and stale.unavailable_reason == "INDEX_STALE"
    assert index.worker.asked == []  # the question is not encoded for a frame that cannot be scored


async def test_a_source_that_cannot_be_read_falls_back_and_is_not_read_again_until_it_changes() -> (
    None
):
    index, spans, _ = build(artifacts=3, per=4)
    index.fail = EmbeddingIndexError("INDEX_INCOMPLETE")
    stamp: list[IndexStamp] = [(True, 1, (("job:1", 1),))]
    provider = make(index, stamp)
    for _ in range(3):
        material = await provider.material("p", "lantern", spans)
        assert material is not None and material.unavailable_reason == "INDEX_INCOMPLETE"
    assert index.reads == 1
    index.fail = None
    stamp[0] = (True, 1, (("job:1", 2),))
    assert scores(await provider.material("p", "lantern", spans))


async def test_a_copy_over_the_size_cap_is_not_kept_and_dense_falls_back() -> None:
    index, spans, _ = build(artifacts=3, per=5)
    windows = sum(len(w) for rows in index.sources.values() for _, _, w in rows)
    fits = make(index, byte_cap=windows * WINDOW_BYTES)
    assert scores(await fits.material("p", "lantern", spans))
    too_big = make(index, byte_cap=windows * WINDOW_BYTES - 1)
    material = await too_big.material("p", "lantern", spans)
    assert material is not None and material.unavailable_reason == "INDEX_TOO_LARGE"
    assert too_big.resident_projects() == () and too_big.resident_bytes() == 0


async def test_only_two_projects_stay_resident() -> None:
    index, spans, _ = build(artifacts=2, per=3)
    provider = make(index)
    for name in ("p", "q", "r"):
        retagged = tuple(s.model_copy(update={"project_id": name}) for s in spans)
        assert await provider.material(name, "lantern", retagged) is not None
    assert provider.resident_projects() == ("q", "r")


ENCODER_FAILURES: list[tuple[str, DenseUnavailableReason]] = [
    ("EMBEDDING_TIMEOUT", "EMBEDDING_TIMEOUT"),
    ("EMBEDDING_INPUT_TOO_LARGE", "QUERY_TOO_LONG"),
    ("EMBEDDING_NOT_PREPARED", "EMBEDDING_NOT_PREPARED"),
    ("EMBEDDING_FAILED", "EMBEDDING_FAILED"),
    ("EMBEDDING_QUEUE_FULL", "EMBEDDING_FAILED"),
]


@pytest.mark.parametrize(("code", "reason"), ENCODER_FAILURES)
async def test_an_encoder_failure_is_a_reason_and_never_an_error(
    code: str, reason: DenseUnavailableReason
) -> None:
    index, spans, _ = build(artifacts=2, per=3)
    index.worker.error = EmbeddingIndexError(code)
    material = await make(index).material("p", "lantern", spans)
    assert material is not None and material.unavailable_reason == reason


async def test_an_unexpected_failure_is_also_only_a_reason() -> None:
    index, spans, _ = build(artifacts=2, per=3)
    index.worker.error = RuntimeError("boom")
    material = await make(index).material("p", "lantern", spans)
    assert material is not None and material.unavailable_reason == "EMBEDDING_FAILED"


async def test_prepared_material_makes_routing_use_dense_and_record_it() -> None:
    index, spans, _ = build(artifacts=4, per=6)
    material = await make(index).material("p", "lantern", spans)
    assert material is not None
    request = RetrievalRequest("lantern", (), spans, dense=material)
    policy = RetrievalRoutingPolicy(max_spans=5)
    result = select_retrieval_context(request, policy)
    assert result.routing is not None
    assert (result.routing.route, result.routing.dense_used, result.routing.fallback_reason) == (
        "RRF_LEX_GB_DENSE",
        True,
        None,
    )
    assert result.routing.dense_material_digest is not None
    without = select_retrieval_context(RetrievalRequest("lantern", (), spans), policy)
    assert without.routing is not None and not without.routing.dense_used
    assert without.routing.fallback_reason == "DENSE_MATERIAL_MISSING"
