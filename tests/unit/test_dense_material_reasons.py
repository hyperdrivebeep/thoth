"""Dense material for the research path: when it is made, when it is not, why dense falls back."""

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast

import pytest
from tests.unit.test_retrieval_policy_v3_research_path import snapshot
from tests.unit.test_retrieval_routing import span

from thoth.application.services.behavior_context import BehaviorWorkContext, behavior_work_scope
from thoth.application.services.dense_material import (
    MAX_DENSE_SPANS,
    WARM_COOLDOWN_SECONDS,
    DenseMaterialProvider,
    IndexStamp,
)
from thoth.application.services.embedding_index_service import EmbeddingIndexService
from thoth.application.services.research_retrieval import (
    dense_for,
    lexical_selection,
    routing_requested,
)
from thoth.domain.behavior_execution import BehaviorUse
from thoth.domain.behavior_policy import RetrievalBehaviorPolicy, RetrievalRoutingPolicy
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.retrieval_embedding import (
    DenseMaterial,
    DenseUnavailableReason,
    dense_question_digest,
    dense_source_digest,
)
from thoth.domain.retrieval_lane import (
    LANES,
    LaneSelection,
    RetrievalRequest,
    select_retrieval_context,
)

READY: IndexStamp = (True, 3, (("job:1", 2),))
POLICY = RetrievalRoutingPolicy(max_spans=2)


def frame(artifacts: int = 3, per: int = 2) -> tuple[EvidenceSpan, ...]:
    return tuple(
        span(a * per + j, f"a{a}", f"text {a}-{j} about a lantern")
        for a in range(artifacts)
        for j in range(per)
    )


def provider(
    *,
    stamp: IndexStamp | Exception = READY,
    index: object | None = None,
    warm: Callable[[], Any] | None = None,
    numpy: bool = True,
    clock: Callable[[], float] = lambda: 0.0,
) -> DenseMaterialProvider:
    def read(_: str) -> IndexStamp:
        if isinstance(stamp, Exception):
            raise stamp
        return stamp

    def load() -> object:
        if not numpy:
            raise ImportError("numpy")
        return object()

    return DenseMaterialProvider(
        stamp=read,
        index=lambda: cast(EmbeddingIndexService | None, index),
        warm=warm,
        numpy_loader=load,
        clock=clock,
    )


def unprepared_index() -> object:
    return SimpleNamespace(worker=SimpleNamespace(prepared=False), on_change=None)


async def test_a_project_that_is_off_and_a_single_source_get_no_material() -> None:
    off = provider(stamp=(False, 4, ()))
    assert await off.material("p", "lantern", frame()) is None
    on = provider()
    assert await on.material("p", "lantern", frame(artifacts=1, per=3)) is None
    assert await on.material("p", "lantern", ()) is None
    assert await on.material("other-project", "lantern", frame()) is None


REASONS: list[tuple[str, Callable[[], DenseMaterialProvider], DenseUnavailableReason]] = [
    (
        "numpy is not installed",
        lambda: provider(numpy=False),
        "EMBEDDING_DEPENDENCY_UNAVAILABLE",
    ),
    ("no source is READY yet", lambda: provider(stamp=(True, 1, ())), "INDEX_PENDING"),
    ("the encoder was never started", lambda: provider(index=None), "EMBEDDING_NOT_PREPARED"),
    (
        "the encoder is not loaded",
        lambda: provider(index=unprepared_index()),
        "EMBEDDING_NOT_PREPARED",
    ),
    ("the index state cannot be read", lambda: provider(stamp=OSError("db")), "EMBEDDING_FAILED"),
]


@pytest.mark.parametrize(("name", "make", "reason"), REASONS, ids=[r[0] for r in REASONS])
async def test_each_reason_reaches_the_routing_observation(
    name: str, make: Callable[[], DenseMaterialProvider], reason: DenseUnavailableReason
) -> None:
    evidence = frame()
    material = await make().material("p", "lantern", evidence)
    assert material is not None and material.unavailable_reason == reason and not material.scores
    # The binding is real, so routing reports the reason and not a mismatch.
    assert material.question_sha256 == dense_question_digest("lantern")
    assert material.source_digest == dense_source_digest(evidence)
    result = select_retrieval_context(
        RetrievalRequest("lantern", (), evidence, dense=material), POLICY
    )
    assert result.routing is not None
    assert (result.routing.route, result.routing.dense_used) == ("RRF_LEX_GB", False)
    assert result.routing.fallback_reason == reason, name
    plain = select_retrieval_context(RetrievalRequest("lantern", (), evidence), POLICY)
    assert result.selected == plain.selected
    assert plain.routing is not None and plain.routing.fallback_reason == "DENSE_MATERIAL_MISSING"


async def test_a_frame_beyond_the_material_limit_falls_back_with_a_reason() -> None:
    many = tuple(span(i, f"a{i % 3}", f"t{i}") for i in range(MAX_DENSE_SPANS + 1))
    material = await provider().material("p", "lantern", many)
    assert material is not None and material.unavailable_reason == "INDEX_TOO_LARGE"


async def test_the_encoder_is_started_in_the_background_once_per_cooldown() -> None:
    now = [0.0]
    started: list[int] = []

    async def warm() -> None:
        started.append(1)
        raise RuntimeError("model folder is gone")  # a failed start must not fail a request

    p = provider(index=None, warm=warm, clock=lambda: now[0])
    for _ in range(3):
        material = await p.material("p", "lantern", frame())
        assert material is not None and material.unavailable_reason == "EMBEDDING_NOT_PREPARED"
        assert p.warming is not None
        await p.warming
    assert started == [1]
    now[0] = WARM_COOLDOWN_SECONDS + 1
    await p.material("p", "lantern", frame())
    assert p.warming is not None
    await p.warming
    assert started == [1, 1]


# --- the research path ----------------------------------------------------------------------


class Source:
    def __init__(self, material: DenseMaterial | None) -> None:
        self.material_value = material
        self.calls: list[tuple[str, str, tuple[EvidenceSpan, ...]]] = []

    async def material(
        self, project_id: str, question: str, evidence: tuple[EvidenceSpan, ...]
    ) -> DenseMaterial | None:
        self.calls.append((project_id, question, evidence))
        return self.material_value


WORK = cast(Any, SimpleNamespace(effective_question="lantern"))


def scope(active: object, *shadows: object) -> BehaviorWorkContext:
    return BehaviorWorkContext(
        "p",
        (snapshot(cast(Any, active)),),
        shadows=tuple(snapshot(cast(Any, p), "SHADOW") for p in shadows),
    )


async def test_material_is_asked_for_only_when_a_routing_policy_can_read_it() -> None:
    evidence = frame()
    source = Source(None)
    with behavior_work_scope(scope(RetrievalBehaviorPolicy())):
        assert not routing_requested()
        assert await dense_for(source, WORK, "p", evidence) is None
    assert source.calls == []
    with behavior_work_scope(scope(RetrievalBehaviorPolicy(), POLICY)):
        assert routing_requested()
        await dense_for(source, WORK, "p", evidence)
    with behavior_work_scope(scope(POLICY)):
        assert routing_requested()
    assert source.calls == [("p", "lantern", evidence)]
    assert await dense_for(None, WORK, "p", evidence) is None
    assert not routing_requested()  # outside any scope


def material_of(evidence: tuple[EvidenceSpan, ...]) -> DenseMaterial:
    return DenseMaterial.prepared(
        project_id="p",
        question_sha256=dense_question_digest("lantern"),
        source_digest=dense_source_digest(connected(evidence)),
        scores=(),
        unavailable_reason="INDEX_PENDING",
    )


def connected(evidence: tuple[EvidenceSpan, ...]) -> tuple[EvidenceSpan, ...]:
    from thoth.domain.evidence import connected_retrieval_spans

    return connected_retrieval_spans(evidence)


def legacy_uses(evidence: tuple[EvidenceSpan, ...], *shadows: object) -> list[BehaviorUse]:
    """What lexical_selection recorded before it could carry dense: the same request without it."""
    from time import perf_counter_ns

    from thoth.application.services.research_retrieval_policy import (
        current_glossary,
        record_selection,
        retrieval_policy,
        retrieval_shadows,
    )

    def stage(selection: LaneSelection) -> str:
        return (
            "FULL_ELIGIBLE_EXTRACTED_CATALOG"
            if selection.mode == "FULL_ELIGIBLE_EXTRACTED_CATALOG"
            else "LEXICAL_CANDIDATES"
        )

    def inputs_of(selection: LaneSelection) -> dict[str, object]:
        inputs: dict[str, object] = {"question": "lantern", "queries": (), "evidence": evidence}
        if selection.provenance:
            inputs["lane"] = dict(selection.provenance)
        if selection.routing is not None:
            inputs["routing"] = selection.routing.model_dump(mode="json")
        return inputs

    request = RetrievalRequest("lantern", (), evidence, (), None, current_glossary())
    started = perf_counter_ns()
    selection = select_retrieval_context(request, retrieval_policy())
    record_selection(stage(selection), inputs_of(selection), selection.selected, started)
    for shadow, shadow_policy in retrieval_shadows():
        assert shadow_policy is not None
        replayed = select_retrieval_context(request, shadow_policy)
        record_selection(stage(replayed), inputs_of(replayed), replayed.selected, started, shadow)
    from thoth.application.services.behavior_context import current_behavior_work

    work = current_behavior_work()
    assert work is not None
    return list(work.uses)


def digests(uses: list[BehaviorUse]) -> list[tuple[str, str, str, str]]:
    return [(u.component, u.operation, u.input_digest, u.output_digest) for u in uses]


def test_a_project_without_dense_records_exactly_what_it_recorded_before() -> None:
    evidence = frame(artifacts=4, per=3)
    for shadows in ((), (POLICY,), (POLICY, RetrievalBehaviorPolicy(max_spans=1))):
        before_context = scope(RetrievalBehaviorPolicy(max_spans=3), *shadows)
        with behavior_work_scope(before_context):
            expected = digests(legacy_uses(evidence))
        now_context = scope(RetrievalBehaviorPolicy(max_spans=3), *shadows)
        with behavior_work_scope(now_context):
            selected = lexical_selection("lantern", (), evidence).selected
        assert digests(now_context.uses) == expected
        assert [s.span_id for s in selected] == [
            s.span_id
            for s in select_retrieval_context(
                RetrievalRequest("lantern", (), evidence), RetrievalBehaviorPolicy(max_spans=3)
            ).selected
        ]


def test_the_default_policy_ignores_the_material_so_an_enabled_project_records_the_same() -> None:
    evidence = frame(artifacts=4, per=3)
    material = material_of(evidence)
    without, with_material = (
        scope(RetrievalBehaviorPolicy(max_spans=3)),
        scope(RetrievalBehaviorPolicy(max_spans=3)),
    )
    with behavior_work_scope(without):
        plain = lexical_selection("lantern", (), evidence)
    with behavior_work_scope(with_material):
        given = lexical_selection("lantern", (), evidence, dense=material)
    assert given.selected == plain.selected
    assert digests(with_material.uses) == digests(without.uses)


def test_a_routing_shadow_receives_the_material_the_request_carries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = frame(artifacts=4, per=3)
    material = material_of(evidence)
    seen: list[DenseMaterial | None] = []

    def spy(
        request: RetrievalRequest, eligible: tuple[EvidenceSpan, ...]
    ) -> tuple[EvidenceSpan, ...]:
        seen.append(request.dense)
        return eligible

    monkeypatch.setitem(LANES, "DENSE", spy)
    for dense in (None, material):
        context = scope(RetrievalBehaviorPolicy(max_spans=3), POLICY)
        with behavior_work_scope(context):
            lexical_selection("lantern", (), evidence, dense=dense)
        assert len(context.uses) == 2
    # Without material the lane is asked and finds none; with it the lane sees that very object.
    assert seen == [None, material]
