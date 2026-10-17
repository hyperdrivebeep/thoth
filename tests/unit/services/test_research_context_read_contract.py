"""Context read ordering, failure priority and basis mutations through the existing service."""

from typing import cast

import pytest
from tests.unit.services.research_context_support import (
    HASH,
    OBJECT_ID,
    PROJECT_ID,
    T0,
    Harness,
    action_plan,
    assessment,
    hypothesis,
    portfolio,
)

from thoth.application.services.action_projection import full_action_plan, full_action_portfolio
from thoth.application.services.hypothesis_projection import full_portfolio
from thoth.domain.enums import EntityType
from thoth.domain.research_basis import BasisCurrentness
from thoth.domain.research_identity import ResearchIdentityError


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("same_work_project", [False, True])
def test_complete_read_order_and_basis_update(
    legacy: bool,
    same_work_project: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness(monkeypatch, work_project=PROJECT_ID if same_work_project else "other")
    first = h.standard(legacy=legacy)
    h.add(EntityType.HYPOTHESIS, "test", assessment(first))
    result = h.read()
    expected = ["object"]
    if same_work_project:
        expected.append(f"capture:DECISION_OBJECT:{OBJECT_ID}")
    for key in sorted(h.labels.values()):
        expected.extend(["revision:" + key, "snapshot:" + key, "index:" + key])
        if same_work_project:
            expected.append("capture:" + key)
        expected.append("freshness:" + key)
        if key in h.typed:
            expected.append(h.typed[key])
    expected.extend(["assessment:test", "prediction:prediction", "prediction.require"])
    if same_work_project:
        expected.append("work.currentness")
    assert h.events == expected
    assert [record.hypothesis_id for record in result.hypotheses] == ["h1", "h2"]
    assert [record.action_id for record in result.actions] == ["a", "b"]
    assert result.portfolio_id == "hp" and result.action_portfolio_id == "ap"
    assert result.portfolio_view is not None and result.action_plan_view is not None
    assert result.unrepresented_refs == (("h1", "h2") if legacy else ())
    assert len(result.test_assessments) == 1
    assert result.heads == h.heads
    h.heads["LATE:change"] = HASH
    assert "LATE:change" not in result.heads
    with pytest.raises(TypeError):
        cast(dict[str, str], result.heads)["forbidden"] = HASH
    with pytest.raises(TypeError):
        cast(dict[str, BasisCurrentness], result.currentness)["forbidden"] = BasisCurrentness(
            state="CURRENT"
        )
    if same_work_project:
        assert h.work.context["entity_currentness"] == {
            key: value.model_dump(mode="json") for key, value in result.currentness.items()
        }
    else:
        assert h.work.context == {"sentinel": "kept"} and h.work.consumed_heads == {}


@pytest.mark.parametrize("kind", ["missing", "mismatch"])
def test_object_failure_has_no_capture_or_following_reads(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(monkeypatch)
    h.standard()
    if kind == "missing":
        h.object = None
    else:
        h.heads[f"DECISION_OBJECT:{OBJECT_ID}"] = "b" * 64
    with pytest.raises(ResearchIdentityError, match="RESEARCH_OBJECT_HEAD_MISMATCH"):
        h.read()
    assert h.events == ["object"] and h.work.consumed_heads == {}
    assert h.work.context == {"sentinel": "kept"}


@pytest.mark.parametrize(
    ("case", "code", "last"),
    [
        ("revision", "RESEARCH_IDENTITY_SOURCE_MISSING", "revision:HYPOTHESIS:bad"),
        ("snapshot", "RESEARCH_IDENTITY_SOURCE_MISSING", "snapshot:HYPOTHESIS:bad"),
        ("head", "RESEARCH_HEAD_IDENTITY_MISMATCH", "snapshot:HYPOTHESIS:bad"),
        ("index", "RESEARCH_IDENTITY_INDEX_MISMATCH", "index:HYPOTHESIS:bad"),
        ("legacy", "LEGACY_SCHEMA_UNRESOLVED", "index:HYPOTHESIS:bad"),
        ("decode", "RESEARCH_SNAPSHOT_DIGEST_MISMATCH", "snapshot:HYPOTHESIS:bad"),
    ],
)
def test_read_error_precedence_and_no_work_publication(
    case: str,
    code: str,
    last: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness(monkeypatch)
    digest = h.add(EntityType.HYPOTHESIS, "bad", {"unknown": "legacy"})
    if case in {"revision", "snapshot", "head"}:
        h.heads["HYPOTHESIS:wrong"] = h.heads.pop("HYPOTHESIS:bad")
    if case == "revision":
        del h.revisions[digest]
    elif case == "snapshot":
        del h.snapshots["snapshot:HYPOTHESIS:bad"]
    elif case == "decode":
        h.snapshots["snapshot:HYPOTHESIS:bad"] = h.snapshots["snapshot:HYPOTHESIS:bad"].model_copy(
            update={"content_digest": "f" * 64}
        )
    if case != "legacy":
        h.indexed[digest] = h.indexed[digest].model_copy(update={"object_id": "mismatched"})
    with pytest.raises(ResearchIdentityError) as caught:
        h.read()
    assert caught.value.reason_code == code
    assert h.events[-1] == last
    assert h.events.count(f"capture:DECISION_OBJECT:{OBJECT_ID}") == 1
    assert not any(event.startswith("freshness:") for event in h.events)
    assert h.work.context == {"sentinel": "kept"}


def test_other_object_is_checked_but_not_captured_or_projected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness(monkeypatch)
    h.add(EntityType.HYPOTHESIS, "foreign", hypothesis("foreign", object_id="other"), "hypothesis")
    result = h.read()
    assert h.events == [
        "object",
        f"capture:DECISION_OBJECT:{OBJECT_ID}",
        "revision:HYPOTHESIS:foreign",
        "snapshot:HYPOTHESIS:foreign",
        "index:HYPOTHESIS:foreign",
        "work.currentness",
    ]
    assert result.hypotheses == () and result.currentness == {}


def test_noncurrent_assessment_is_skipped_before_eligibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness(monkeypatch)
    first = h.standard()
    h.add(EntityType.HYPOTHESIS, "test", assessment(first))
    h.states["HYPOTHESIS:test"] = BasisCurrentness(state="REVIEW_REQUIRED")
    result = h.read()
    assert result.test_assessments == () and result.unrepresented_refs == ()
    assert "assessment:test" not in h.events and "prediction:prediction" not in h.events
    assert result.currentness["HYPOTHESIS:test"].state == "REVIEW_REQUIRED"
    assert h.events[-1] == "work.currentness"


@pytest.mark.parametrize(
    "case",
    ["no_lifecycle", "missing_hypothesis", "unbound_prediction", "semantic_mismatch", "admitted"],
)
def test_eligibility_guards_and_validation_order(
    case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(monkeypatch, lifecycle=case != "no_lifecycle")
    first = h.standard()
    updates: dict[str, object] = {
        "hypothesis_id": "missing" if case == "missing_hypothesis" else "h1",
        "prediction_id": "unbound" if case == "unbound_prediction" else "prediction",
    }
    if case == "semantic_mismatch":
        updates["hypothesis_semantic_digest"] = "f" * 64
    h.add(EntityType.HYPOTHESIS, "test", assessment(first, **updates))
    result = h.read()
    if case == "admitted":
        assert h.events[-4:] == [
            "assessment:test",
            "prediction:prediction",
            "prediction.require",
            "work.currentness",
        ]
        assert [item.assessment_id for item in result.test_assessments] == ["test"]
    else:
        assert result.test_assessments == () and result.unrepresented_refs == ("test",)
        assert "assessment:test" not in h.events and "prediction:prediction" not in h.events
        assert h.events[-1] == "work.currentness"


def test_missing_prediction_stops_before_require_prediction_and_work_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness(monkeypatch)
    first = h.standard()
    h.add(EntityType.HYPOTHESIS, "test", assessment(first))
    h.missing_prediction = True
    with pytest.raises(ResearchIdentityError, match="TEST_CONTEXT_PREDICTION_MISSING"):
        h.read()
    assert h.events[-2:] == ["assessment:test", "prediction:prediction"]
    assert h.work.context == {"sentinel": "kept"}


@pytest.mark.parametrize(
    ("kind", "code"),
    [
        ("portfolio", "RESEARCH_PORTFOLIO_SELECTION_REQUIRED"),
        ("plan", "RESEARCH_ACTION_PLAN_SELECTION_REQUIRED"),
        ("action_portfolio", "RESEARCH_ACTION_PORTFOLIO_SELECTION_REQUIRED"),
    ],
)
def test_selection_errors_precede_eligibility_and_work_update(
    kind: str, code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(monkeypatch)
    first = h.standard()
    h.add(EntityType.HYPOTHESIS, "test", assessment(first))
    h.fault_at = "assessment:test"
    if kind == "portfolio":
        h.add(
            EntityType.HYPOTHESIS,
            "hp2",
            full_portfolio(
                portfolio("hp2"),
                project_id=PROJECT_ID,
                revision_id="extra",
                created_at=T0,
                parent=None,
            ),
            "hypothesis.portfolio",
        )
    if kind in {"portfolio", "plan"}:
        h.add(
            EntityType.ACTION,
            "plan2",
            full_action_plan(
                action_plan("plan2"),
                project_id=PROJECT_ID,
                portfolio_id="ap",
                revision_id="extra",
                created_at=T0,
                parent=None,
                actions=(),
            ),
            "action.plan",
        )
    h.add(
        EntityType.ACTION,
        "ap2",
        full_action_portfolio(
            action_plan(),
            project_id=PROJECT_ID,
            portfolio_id="ap2",
            revision_id="extra",
            created_at=T0,
            parent=None,
            retained_refs=(),
        ),
        "action.portfolio",
    )
    with pytest.raises(ResearchIdentityError) as caught:
        h.read()
    assert caught.value.reason_code == code
    assert "assessment:test" not in h.events and h.work.context == {"sentinel": "kept"}


@pytest.mark.parametrize(
    "point",
    [
        "object",
        "revision:HYPOTHESIS:h1",
        "snapshot:HYPOTHESIS:h1",
        "index:HYPOTHESIS:h1",
        "freshness:HYPOTHESIS:h1",
        "hypothesis:h1",
        "assessment:test",
        "prediction:prediction",
        "prediction.require",
    ],
)
def test_read_fault_is_not_wrapped_and_does_not_update_work(
    point: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(monkeypatch)
    first = h.standard()
    h.add(EntityType.HYPOTHESIS, "test", assessment(first))
    h.fault_at = point
    with pytest.raises(RuntimeError) as caught:
        h.read()
    assert caught.value is h.fault and h.events[-1] == point
    assert h.work.context == {"sentinel": "kept"}
