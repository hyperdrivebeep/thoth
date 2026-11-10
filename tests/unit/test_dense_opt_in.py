"""A project that switched local retrieval on: its policy, the lexical fall-back, nothing else."""

import hashlib
from collections.abc import Callable
from dataclasses import replace
from typing import cast

import pytest
from tests.unit.test_retrieval_routing import FakeEncoder, material, span

from thoth.adapters.behavior.registry import default_behavior_components
from thoth.application.services.behavior_context import (
    BehaviorWorkContext,
    behavior_work_scope,
    select_evidence_for_behavior,
)
from thoth.application.services.behavior_exposure_compilation import compile_exposure_policy
from thoth.application.services.behavior_snapshot_service import BehaviorSnapshotService
from thoth.application.services.evidence_context import select_evidence_context
from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.behavior_execution import ActiveBehaviorSnapshot, BehaviorSelection
from thoth.domain.behavior_policy import (
    BehaviorPolicy,
    BehaviorPolicyError,
    RetrievalBehaviorPolicy,
    RetrievalLanePolicy,
    RetrievalRoutingPolicy,
)
from thoth.domain.canonical import canonical_payload
from thoth.domain.evaluation_run import sealed_payload
from thoth.domain.evidence import connected_retrieval_spans
from thoth.domain.retrieval_embedding import (
    DenseMaterial,
    dense_question_digest,
    dense_source_digest,
)
from thoth.domain.retrieval_lane import RetrievalRequest, select_retrieval_context
from thoth.ports.behavior_artifact import BehaviorArtifactStorePort
from thoth.ports.behavior_execution import BehaviorWorkControlPort

RETRIEVAL = BehaviorArtifactKind.RETRIEVAL_POLICY

# What this code gave before a project could opt in (taken from the commit before this change):
# the snapshots of a project with no opt-in, the whole selection, and the default routing policy.
BEFORE = {
    "PROMPT_BUNDLE": "2edb3839b1d3ec1b3a3d683c4024c148a8cd85916c33d8c9a38a6e622943742b",
    "RETRIEVAL_POLICY": "e0b0ccf1009c6bd602eaa1155166199d2b9db9a32551fc2e04d5db228313987c",
    "WORKFLOW_DEFINITION": "98de1bfdf2990ab8109fa86fdb9945328eb6f00a6882dfbf531a37e0789087eb",
}
BEFORE_SELECTION = "d3e1bc33544dcd74634302556d749cca5ba283c37022d5d83acb898f0b3ef3e6"
BEFORE_ROUTING_POLICY = "736bc6859191092218d7549cf65073afc25f128336152a257e2f42e8d7d0b9e2"


class EmptyStore:
    """A store with no artifact and no registry pointer: every baseline is the built-in default."""

    def list(self, project_id: str, kind: object = None) -> tuple[object, ...]:
        return ()

    def read_registry(self, project_id: str, kind: object) -> None:
        return None


class Control:
    """The exposure runtime stand-in: it hands back what the test chose to select."""

    def __init__(
        self,
        edit: Callable[[ActiveBehaviorSnapshot], ActiveBehaviorSnapshot] | None = None,
        shadows: tuple[ActiveBehaviorSnapshot, ...] = (),
    ) -> None:
        self.edit, self.shadows = edit, shadows

    def baseline(self, *_args: object) -> None:
        return None

    def begin(
        self,
        project: str,
        thread: str,
        execution: str,
        baselines: tuple[ActiveBehaviorSnapshot, ...],
    ) -> BehaviorSelection:
        edit = self.edit
        chosen = (
            baselines
            if edit is None
            else tuple(edit(s) if s.component == RETRIEVAL else s for s in baselines)
        )
        return BehaviorSelection(snapshots=chosen, shadows=self.shadows)

    def finish(self, *_args: object) -> None:
        return None


def service(
    opt_in: Callable[[str], bool] | None, control: Control | None = None
) -> BehaviorSnapshotService:
    value = BehaviorSnapshotService(
        cast(BehaviorArtifactStorePort, EmptyStore()), default_behavior_components()
    )
    value.retrieval_opt_in = opt_in
    value.control = cast(BehaviorWorkControlPort, control)
    return value


def sha(value: object) -> str:
    return hashlib.sha256(canonical_payload(value)).hexdigest()  # type: ignore[arg-type]


def retrieval_of(selection: BehaviorSelection) -> ActiveBehaviorSnapshot:
    return next(s for s in selection.snapshots if s.component == RETRIEVAL)


def snapshot_with(
    base: ActiveBehaviorSnapshot, policy: BehaviorPolicy, origin: str
) -> ActiveBehaviorSnapshot:
    data = base.model_dump(mode="python", exclude={"snapshot_digest"})
    data.update(policy=policy, origin=origin)
    return ActiveBehaviorSnapshot.model_validate(
        sealed_payload("ACTIVE_BEHAVIOR_SNAPSHOT", "snapshot_digest", data)
    )


def prepare(
    opt_in: Callable[[str], bool] | None, control: Control | None = None
) -> BehaviorSelection:
    return service(opt_in, control).prepare_work("project:p", "thread:t", "exec:1")


# --- nothing changes unless the project opted in -----------------------------------------------


def off(_project: str) -> bool:
    return False


def raising(_project: str) -> bool:
    raise OSError("the index state could not be read")


@pytest.mark.parametrize("opt_in", [None, off, raising], ids=["none", "off", "raises"])
def test_a_project_that_did_not_opt_in_gets_exactly_the_snapshots_it_got_before(
    opt_in: Callable[[str], bool] | None,
) -> None:
    selection = prepare(opt_in)
    assert {s.component.value: s.snapshot_digest for s in selection.snapshots} == BEFORE
    assert sha(selection) == BEFORE_SELECTION and selection.shadows == ()
    assert retrieval_of(selection).origin == "BUILTIN_DEFAULT"


def test_the_default_routing_policy_and_its_shadow_guard_keep_their_bytes() -> None:
    assert sha(RetrievalRoutingPolicy()) == BEFORE_ROUTING_POLICY
    assert RetrievalRoutingPolicy().exposure == "SHADOW_ONLY"
    handler = default_behavior_components().resolve(RETRIEVAL, "5.0.0")
    shadow = RetrievalRoutingPolicy().model_dump()
    assert compile_exposure_policy(handler, shadow, "SHADOW").version == "5.0.0"
    for stage in ("CANARY", "BASELINE"):
        with pytest.raises(BehaviorPolicyError, match="RETRIEVAL_ROUTING_SHADOW_ONLY"):
            compile_exposure_policy(handler, shadow, stage)


def test_no_stored_content_or_exposure_can_carry_the_project_opt_in_policy() -> None:
    handler = default_behavior_components().resolve(RETRIEVAL, "5.0.0")
    content = RetrievalRoutingPolicy(exposure="PROJECT_OPT_IN").model_dump()
    with pytest.raises(BehaviorPolicyError, match="BEHAVIOR_POLICY_INVALID"):
        handler.compile(content)
    for stage in ("SHADOW", "CANARY", "BASELINE"):
        with pytest.raises(BehaviorPolicyError):
            compile_exposure_policy(handler, content, stage)


# --- the project opted in -----------------------------------------------------------------------


def test_an_opted_in_project_gets_its_own_routing_policy_and_nothing_else_changes() -> None:
    asked: list[str] = []

    def ready(project: str) -> bool:
        asked.append(project)
        return True

    selection = prepare(ready)
    assert asked == ["project:p"]
    current = retrieval_of(selection)
    assert current.origin == "PROJECT_OPT_IN" and current.reason_code == "LOCAL_RETRIEVAL_ENABLED"
    assert current.artifact_ref is None and current.exposure_ref is None
    assert current.policy == RetrievalRoutingPolicy(
        max_spans=160, character_budget=50000, exposure="PROJECT_OPT_IN"
    )
    assert {
        s.component.value: s.snapshot_digest
        for s in selection.snapshots
        if s.component != RETRIEVAL
    } == {k: v for k, v in BEFORE.items() if k != "RETRIEVAL_POLICY"}
    assert current.snapshot_digest != BEFORE["RETRIEVAL_POLICY"]
    again = ActiveBehaviorSnapshot.model_validate(current.model_dump(mode="python"))
    assert again == current and sha(again) == sha(current)


def test_the_budgets_of_the_baseline_it_replaces_are_kept() -> None:
    control = Control(
        lambda s: snapshot_with(
            s, RetrievalBehaviorPolicy(max_spans=7, character_budget=900), "BASELINE"
        )
    )
    current = retrieval_of(prepare(lambda _p: True, control))
    assert current.origin == "PROJECT_OPT_IN"
    assert isinstance(current.policy, RetrievalRoutingPolicy)
    assert (current.policy.max_spans, current.policy.character_budget) == (7, 900)


def test_a_canary_or_a_promoted_baseline_is_not_replaced_and_the_index_is_not_even_read() -> None:
    reads: list[str] = []

    def read(project: str) -> bool:
        reads.append(project)  # an error here would be swallowed, so the calls are counted instead
        return True

    canary = Control(lambda s: snapshot_with(s, RetrievalBehaviorPolicy(max_spans=3), "CANARY"))
    kept_canary = retrieval_of(prepare(read, canary))
    assert kept_canary.origin == "CANARY"
    assert isinstance(kept_canary.policy, RetrievalBehaviorPolicy)
    assert kept_canary.policy.max_spans == 3
    promoted = Control(lambda s: snapshot_with(s, RetrievalLanePolicy(max_spans=3), "BASELINE"))
    kept = retrieval_of(prepare(read, promoted))
    assert kept.origin == "BASELINE" and isinstance(kept.policy, RetrievalLanePolicy)
    assert reads == []


def test_shadows_stay_and_capture_and_baseline_snapshots_stay_the_baseline() -> None:
    plain = service(None).capture("project:p")
    shadow = snapshot_with(
        retrieval_of(BehaviorSelection(snapshots=plain)), RetrievalRoutingPolicy(), "SHADOW"
    )
    opted = service(lambda _p: True, Control(shadows=(shadow,)))
    selection = opted.prepare_work("project:p", "thread:t", "exec:1")
    assert selection.shadows == (shadow,) and retrieval_of(selection).origin == "PROJECT_OPT_IN"
    assert opted.capture("project:p") == plain
    assert opted.baseline_snapshot("project:p", RETRIEVAL, "thread:t") == retrieval_of(
        BehaviorSelection(snapshots=plain)
    )


# --- a question whose dense scores cannot be used gets the lexical selection --------------------

POLICY = RetrievalRoutingPolicy(max_spans=3, exposure="PROJECT_OPT_IN")
LEXICAL = RetrievalBehaviorPolicy(max_spans=3)
TEXTS = [
    "orange lantern evidence in this sentence",
    "quiet stones by the stream",
    "lantern",
    "orange orange orange",
    "a stream of orange light",
    "evidence evidence",
]


def frame() -> RetrievalRequest:
    spans = tuple(span(i, "a" if i % 2 == 0 else "b", text) for i, text in enumerate(TEXTS))
    return RetrievalRequest("orange lantern", (), spans)


def test_the_frame_is_one_where_rrf_of_lex_and_gb_differs_from_lex() -> None:
    req = frame()
    lexical = [s.span_id for s in select_retrieval_context(req, LEXICAL).selected]
    rrf = [
        s.span_id
        for s in select_retrieval_context(req, RetrievalRoutingPolicy(max_spans=3)).selected
    ]
    assert lexical == ["s0", "s4"] and rrf == ["s0", "s4", "s2"]


UNAVAILABLE = [
    "MODEL_NOT_INSTALLED",
    "EMBEDDING_DEPENDENCY_UNAVAILABLE",
    "INDEX_PENDING",
    "INDEX_STALE",
    "INDEX_INCOMPLETE",
    "INDEX_TOO_LARGE",
    "QUERY_TOO_LONG",
    "EMBEDDING_TIMEOUT",
    "EMBEDDING_FAILED",
    "EMBEDDING_NOT_PREPARED",
]


def assert_lexical(result: object, req: RetrievalRequest, reason: str) -> None:
    from thoth.domain.retrieval_lane import LaneSelection

    assert isinstance(result, LaneSelection)
    wanted = select_retrieval_context(req, LEXICAL)
    assert result.selected == wanted.selected and result.mode == wanted.mode
    assert result.provenance == wanted.provenance and result.eligible == wanted.eligible
    assert result.routing is not None
    assert (result.routing.route, result.routing.dense_used) == ("LEXICAL", False)
    assert result.routing.fallback_reason == reason and result.routing.artifact_count == 2


def test_without_dense_material_the_selection_is_the_lexical_one_with_the_reason() -> None:
    req = frame()
    assert_lexical(select_retrieval_context(req, POLICY), req, "DENSE_MATERIAL_MISSING")


@pytest.mark.parametrize("reason", UNAVAILABLE)
def test_each_reason_the_scores_cannot_be_used_gives_the_lexical_selection(reason: str) -> None:
    req = frame()
    dense = DenseMaterial.prepared(
        project_id="p",
        question_sha256=dense_question_digest(req.question),
        source_digest=dense_source_digest(connected_retrieval_spans(req.evidence)),
        unavailable_reason=reason,  # type: ignore[arg-type]
    )
    assert_lexical(select_retrieval_context(replace(req, dense=dense), POLICY), req, reason)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"question_sha256": "0" * 64}, "DENSE_QUESTION_MISMATCH"),
        ({"source_digest": "0" * 64}, "INDEX_STALE"),
        ({"onnx_sha256": "0" * 64}, "MODEL_HASH_MISMATCH"),
        ({"scores": ()}, "INDEX_INCOMPLETE"),
    ],
)
def test_material_that_does_not_match_the_frame_gives_the_lexical_selection(
    change: dict[str, object], reason: str
) -> None:
    req = frame()
    dense = material(req, FakeEncoder()).model_copy(update=change)
    result = select_retrieval_context(replace(req, dense=dense), POLICY)
    assert_lexical(result, req, reason)
    assert result.routing is not None and result.routing.dense_material_digest is not None


def test_usable_scores_give_the_fused_selection_that_the_shadow_policy_would() -> None:
    req = replace(frame())
    req = replace(req, dense=material(req, FakeEncoder()))
    used = select_retrieval_context(req, POLICY)
    shadow = select_retrieval_context(req, RetrievalRoutingPolicy(max_spans=3))
    assert used.selected == shadow.selected
    assert used.routing == shadow.routing
    assert (
        used.routing is not None
        and used.routing.route == "RRF_LEX_GB_DENSE"
        and used.routing.dense_used
    )


def test_the_shadow_policy_still_falls_back_to_rrf_of_lex_and_gb() -> None:
    req = frame()
    result = select_retrieval_context(req, RetrievalRoutingPolicy(max_spans=3))
    assert result.routing is not None and result.routing.route == "RRF_LEX_GB"
    assert [s.span_id for s in result.selected] == ["s0", "s4", "s2"]


def test_one_source_is_what_it_was_for_the_project_policy() -> None:
    req = replace(frame(), evidence=tuple(span(i, "a", t) for i, t in enumerate(TEXTS)))
    a = select_retrieval_context(req, POLICY)
    b = select_retrieval_context(req, RetrievalRoutingPolicy(max_spans=3))
    assert a.selected == b.selected and a.routing == b.routing
    assert (
        a.routing is not None
        and a.routing.artifact_count == 1
        and a.routing.fallback_reason is None
    )


def test_a_wrong_project_is_still_an_error_for_the_project_policy() -> None:
    req = frame()
    dense = material(req, FakeEncoder()).model_copy(update={"project_id": "another"})
    with pytest.raises(ValueError, match="PROJECT_SCOPE"):
        select_retrieval_context(replace(req, dense=dense), POLICY)


# --- the older analysis entry ----------------------------------------


def context_with(policy: RetrievalRoutingPolicy) -> BehaviorWorkContext:
    base = retrieval_of(BehaviorSelection(snapshots=service(None).capture("project:p")))
    origin = "PROJECT_OPT_IN" if policy.exposure == "PROJECT_OPT_IN" else "SHADOW"
    return BehaviorWorkContext("project:p", (snapshot_with(base, policy, origin),))


def test_the_older_entry_selects_lexically_with_the_budgets_of_a_project_policy() -> None:
    spans = frame().evidence
    context = context_with(POLICY)
    with behavior_work_scope(context):
        chosen = select_evidence_for_behavior(problem="orange lantern", evidence=spans)
    wanted = select_evidence_context(
        problem="orange lantern", evidence=spans, max_spans=3, character_budget=50000
    )
    assert chosen.selected == wanted.selected
    (use,) = context.uses
    assert use.operation == "EVIDENCE_SELECTION"
    assert (
        use.snapshot_digest
        == retrieval_of(BehaviorSelection(snapshots=context.snapshots)).snapshot_digest
    )


def test_the_older_entry_still_refuses_a_routing_policy_that_is_only_a_shadow() -> None:
    shadow_only = context_with(RetrievalRoutingPolicy(max_spans=3))
    with (
        behavior_work_scope(shadow_only),
        pytest.raises(BehaviorPolicyError, match="RETRIEVAL_ROUTING_UNSUPPORTED_ENTRY"),
    ):
        select_evidence_for_behavior(problem="orange", evidence=frame().evidence)
