"""S3: a 3.0.0 policy reaches the research path only through a snapshot."""

from tests.unit.test_post_audit_contracts import SHA, span

from thoth.application.services.behavior_context import (
    BehaviorWorkContext,
    behavior_work_scope,
    select_evidence_for_behavior,
)
from thoth.application.services.research_retrieval import lexical_candidates
from thoth.application.services.research_retrieval_policy import retrieval_policy
from thoth.domain.behavior_execution import ActiveBehaviorSnapshot
from thoth.domain.behavior_policy import (
    RetrievalBehaviorPolicy,
    RetrievalLanePolicy,
)
from thoth.domain.evaluation_run import sealed_payload

POLICIES = (RetrievalBehaviorPolicy, RetrievalLanePolicy)


def snapshot(
    policy: RetrievalBehaviorPolicy | RetrievalLanePolicy, origin: str = "BASELINE"
) -> ActiveBehaviorSnapshot:
    return ActiveBehaviorSnapshot.model_validate(
        sealed_payload(
            "ACTIVE_BEHAVIOR_SNAPSHOT",
            "snapshot_digest",
            {
                "project_id": "p",
                "component": "RETRIEVAL_POLICY",
                "environment": "LOCAL",
                "artifact_ref": None,
                "content_digest": SHA,
                "policy": policy,
                "origin": origin,
                "registry_revision": 0,
                "exposure_ref": None,
                "ignored_registry_digest": None,
                "reason_code": None,
            },
        )
    )


SOURCE = (
    span("header", 1, "단위 ms"),
    span("row1", 2, "alpha twelve milliseconds measured in the first run"),
    span("row2", 3, "alpha fourteen milliseconds measured in the second run"),
    span("foot", 4, "a closing remark that does not match the question"),
)


def test_the_research_path_reads_a_3_0_0_snapshot_and_selects_as_2_0_0_does() -> None:
    chosen: list[list[str]] = []
    for policy in (RetrievalBehaviorPolicy(max_spans=1), RetrievalLanePolicy(max_spans=1)):
        context = BehaviorWorkContext("p", (snapshot(policy),))
        with behavior_work_scope(context):
            assert retrieval_policy() == policy
            chosen.append([s.span_id for s in lexical_candidates("alpha", (), SOURCE)])
        assert len(context.uses) == 1
        assert context.uses[0].snapshot_digest == snapshot(policy).snapshot_digest
    assert chosen[0] == chosen[1] and len(chosen[0]) == 1


def test_a_3_0_0_shadow_is_replayed_and_recorded_without_changing_the_selection() -> None:
    active = snapshot(RetrievalBehaviorPolicy(max_spans=1))
    shadow = snapshot(RetrievalLanePolicy(max_spans=3), origin="SHADOW")
    plain = BehaviorWorkContext("p", (active,))
    with behavior_work_scope(plain):
        wanted = [s.span_id for s in lexical_candidates("alpha", (), SOURCE)]
    context = BehaviorWorkContext("p", (active,), shadows=(shadow,))
    with behavior_work_scope(context):
        actual = [s.span_id for s in lexical_candidates("alpha", (), SOURCE)]
    assert actual == wanted
    assert {u.snapshot_digest for u in context.uses} == {
        active.snapshot_digest,
        shadow.snapshot_digest,
    }
    assert len(context.uses) == 2


def test_the_older_analysis_path_accepts_a_3_0_0_snapshot_and_keeps_its_own_scorer() -> None:
    results: list[list[str]] = []
    for policy in (RetrievalBehaviorPolicy(max_spans=2), RetrievalLanePolicy(max_spans=2)):
        context = BehaviorWorkContext("p", (snapshot(policy),))
        with behavior_work_scope(context):
            selected = select_evidence_for_behavior(problem="alpha", evidence=SOURCE)
        results.append([s.span_id for s in selected.selected])
        assert selected.max_spans == 2
    assert results[0] == results[1]
