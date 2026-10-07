"""Rule-computed verdicts for the requirement trace: states, history, currentness, confirmation."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup
from tests.integration.trace_demo_helpers import (
    POLICY,
    REQ,
    SCORER,
    T0,
    demo_set,
    read_yaml,
)

from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.application.services.request_records import RequestRecords
from thoth.application.services.verification_trace import (
    TraceConflict,
    TraceError,
    VerificationTraceService,
    compute_verdicts,
    dependency_changes,
    mark_stale,
)
from thoth.domain.verification_trace import (
    ChangedDependency,
    Comparator,
    CriterionRule,
    CriterionVerdictState,
    CurrentnessState,
    RequirementVerdictState,
    ResultRecord,
    SelectionPolicy,
    SelectionPolicyName,
    SubjectKind,
    TraceItem,
    TraceKind,
    TraceLink,
    TraceRelation,
    TraceSet,
    VerdictRevision,
    subject_key,
)


def states(verdicts: tuple[VerdictRevision, ...]) -> dict[str, str]:
    return {item.subject_id: item.state.value for item in verdicts}


def by_id(verdicts: tuple[VerdictRevision, ...]) -> dict[str, VerdictRevision]:
    return {item.subject_id: item for item in verdicts}


def run(
    trace_set: TraceSet,
    *,
    previous: tuple[VerdictRevision, ...] = (),
    at: datetime = T0,
    trigger: str = "RECOMPUTE",
    changes: tuple[ChangedDependency, ...] = (),
) -> tuple[VerdictRevision, ...]:
    return compute_verdicts(
        trace_set,
        POLICY,
        computed_at=at,
        previous=previous,
        trigger=trigger,
        changes=changes,
    )


# --- the demo phases ---


@pytest.mark.parametrize("phase", [1, 2])
def test_demo_phases_give_the_states_the_scorer_file_expects(phase: int) -> None:
    expected = read_yaml(SCORER)["phases"][phase]
    got = states(run(demo_set(phase)))
    assert {key: got[key] for key in expected["criteria"]} == expected["criteria"]
    assert {key: got[key] for key in expected["requirement"]} == expected["requirement"]


def test_phase_one_is_held_and_phase_two_fails_with_incomplete_coverage() -> None:
    one = by_id(run(demo_set(1)))
    assert one[REQ].state is RequirementVerdictState.HOLD_INCOMPLETE
    assert one["SYN-C-DET-RAIN"].state is CriterionVerdictState.HOLD_NO_RESULT
    assert one["SYN-C-DET-RAIN"].reasons.computed == ("NO_RESULT:weather=rain",)
    two = by_id(run(demo_set(2)))
    assert two[REQ].state is RequirementVerdictState.FAIL_WITH_INCOMPLETE_COVERAGE
    assert two["SYN-C-DET-RAIN"].reasons.computed == (
        "THRESHOLD_NOT_MET:SYN-RES-SYN-C-DET-RAIN:value=0.80 need >= 0.90 ratio",
    )
    assert two["SYN-C-FA-FOG"].state is CriterionVerdictState.HOLD_NO_RESULT


def test_each_verdict_records_the_rule_conditions_and_results_it_used() -> None:
    dry = by_id(run(demo_set(2)))["SYN-C-DET-DRY"]
    assert dry.conditions.condition == "weather=dry" and dry.conditions.unit == "ratio"
    assert dry.conditions.comparator is Comparator.AT_LEAST and dry.conditions.threshold == Decimal(
        "0.90"
    )
    assert dry.selection is not None
    assert [item.result_id for item in dry.selection.chosen] == ["SYN-RES-SYN-C-DET-DRY"]
    assert dry.selection.policy == POLICY
    assert dry.basis.source_span_refs == ("30_RESULT_DRY_SYNTHETIC.yaml#detection",)
    assert dry.basis.evidence_text_preserved is False
    assert dry.actor.actor_id == "system:verification-trace"
    requirement = by_id(run(demo_set(2)))[REQ]
    assert len(requirement.conditions.required_criteria) == 6
    assert len(requirement.basis.child_verdict_digests) == 6


# --- the states ---


def mini(
    results: list[ResultRecord],
    *,
    comparator: Comparator = Comparator.AT_MOST,
    threshold: str = "0.50",
    unit: str = "per_min",
    required: bool = True,
    with_criterion: bool = True,
) -> TraceSet:
    items = [TraceItem(item_id="REQ-1", item_key="k1", kind=TraceKind.REQUIREMENT)]
    links: list[TraceLink] = []
    rules: list[CriterionRule] = []
    if with_criterion:
        items.append(TraceItem(item_id="C-1", item_key="k2", kind=TraceKind.CRITERION))
        links.append(
            TraceLink(link_id="l1", from_id="C-1", to_id="REQ-1", relation=TraceRelation.REFINES)
        )
        rules.append(
            CriterionRule(
                rule_id="R-1",
                criterion_id="C-1",
                measure="m",
                comparator=comparator,
                threshold=Decimal(threshold),
                unit=unit,
                condition="weather=rain",
                required=required,
            )
        )
    items.extend(
        TraceItem(item_id=item.result_id, item_key=f"k-{item.result_id}", kind=TraceKind.RESULT)
        for item in results
    )
    return TraceSet(
        items=tuple(items), links=tuple(links), rules=tuple(rules), results=tuple(results)
    )


def result(
    rid: str = "RES-1",
    *,
    value: str | None = "0",
    unit: str = "per_min",
    day: int = 0,
    revision: int = 1,
    condition: str = "weather=rain",
    raw: str | None = None,
    numerator: int | None = None,
    denominator: int | None = None,
) -> ResultRecord:
    return ResultRecord(
        result_id=rid,
        result_revision=revision,
        criterion_id="C-1",
        condition=condition,
        value=None if value is None else Decimal(value),
        raw_value=raw,
        unit=unit,
        observed_at=T0 + timedelta(days=day),
        numerator=numerator,
        denominator=denominator,
    )


def test_a_measured_zero_is_a_value_and_an_empty_cell_is_not() -> None:
    zero = by_id(run(mini([result(value="0", raw="0")])))
    assert zero["C-1"].state is CriterionVerdictState.PASS_COMPUTED
    assert (
        by_id(run(mini([result(value="0")], comparator=Comparator.AT_LEAST, threshold="0.90")))[
            "C-1"
        ].state
        is CriterionVerdictState.FAIL_COMPUTED
    )
    empty = by_id(run(mini([result(value=None)])))["C-1"]
    assert empty.state is CriterionVerdictState.HOLD_INVALID_RESULT
    assert empty.reasons.computed == ("VALUE_MISSING:RES-1",)
    blank = by_id(run(mini([result(value=None, raw="  ")])))["C-1"]
    assert blank.reasons.computed == ("VALUE_MISSING:RES-1",)
    word = by_id(run(mini([result(value=None, raw="null")])))["C-1"]
    assert word.reasons.computed == ("VALUE_NOT_A_NUMBER:RES-1",)


def test_a_value_equal_to_the_threshold_meets_both_comparators() -> None:
    at_most = by_id(run(mini([result(value="0.50")])))["C-1"]
    assert at_most.state is CriterionVerdictState.PASS_COMPUTED
    at_least = mini([result(value="0.90")], comparator=Comparator.AT_LEAST, threshold="0.90")
    assert by_id(run(at_least))["C-1"].state is CriterionVerdictState.PASS_COMPUTED
    just_over = by_id(run(mini([result(value="0.51")])))["C-1"]
    assert just_over.state is CriterionVerdictState.FAIL_COMPUTED
    below = mini([result(value="0.89")], comparator=Comparator.AT_LEAST, threshold="0.90")
    assert by_id(run(below))["C-1"].state is CriterionVerdictState.FAIL_COMPUTED


def test_a_wrong_unit_holds_instead_of_comparing() -> None:
    wrong = by_id(run(mini([result(value="0.10", unit="per_hour")])))
    assert wrong["C-1"].state is CriterionVerdictState.HOLD_INVALID_RESULT
    assert wrong["C-1"].reasons.computed == ("UNIT_MISMATCH:RES-1:per_hour!=per_min",)
    assert wrong["REQ-1"].state is RequirementVerdictState.HOLD_INCOMPLETE


def test_counts_that_disagree_with_the_value_hold() -> None:
    bad = by_id(run(mini([result(value="0.10", numerator=7, denominator=10)])))["C-1"]
    assert bad.state is CriterionVerdictState.HOLD_INVALID_RESULT
    assert bad.reasons.computed == ("VALUE_DISAGREES_WITH_COUNTS:RES-1",)
    derived = by_id(run(mini([result(value=None, numerator=3, denominator=10)])))["C-1"]
    assert derived.state is CriterionVerdictState.PASS_COMPUTED
    zero_den = by_id(run(mini([result(value="0.1", numerator=0, denominator=0)])))["C-1"]
    assert zero_den.reasons.computed == ("DENOMINATOR_NOT_POSITIVE:RES-1",)


def test_a_criterion_without_a_rule_holds() -> None:
    trace = mini([])
    trace = TraceSet(items=trace.items, links=trace.links, rules=(), results=())
    verdicts = by_id(run(trace))
    assert verdicts["C-1"].state is CriterionVerdictState.HOLD_NO_RULE
    assert verdicts["REQ-1"].state is RequirementVerdictState.HOLD_INCOMPLETE


def test_an_empty_set_of_required_criteria_is_never_a_pass() -> None:
    nothing = by_id(run(mini([], with_criterion=False)))
    assert nothing["REQ-1"].state is RequirementVerdictState.HOLD_NO_CRITERIA
    assert nothing["REQ-1"].reasons.computed == ("NO_REQUIRED_CRITERIA",)
    optional = by_id(run(mini([result(value="0")], required=False)))
    assert optional["C-1"].state is CriterionVerdictState.PASS_COMPUTED
    assert optional["REQ-1"].state is RequirementVerdictState.HOLD_NO_CRITERIA


def test_requirement_states_and_no_averaging_across_conditions() -> None:
    assert by_id(run(mini([result(value="0")])))["REQ-1"].state is RequirementVerdictState.PASS
    assert by_id(run(mini([result(value="0.9")])))["REQ-1"].state is RequirementVerdictState.FAIL
    # Three conditions: pass, pass, fail. An average of their rates would clear the limit, the rule
    # must not: one failed condition fails the requirement.
    trace = demo_set(2)
    fog = [
        ResultRecord(
            result_id=f"SYN-RES-{cid}",
            criterion_id=cid,
            condition="weather=fog",
            value=Decimal(value),
            unit=unit,
            observed_at=T0 + timedelta(days=3),
        )
        for cid, value, unit in (
            ("SYN-C-DET-FOG", "0.99", "ratio"),
            ("SYN-C-FA-FOG", "0.10", "per_min"),
        )
    ]
    items = (
        *trace.items,
        *(
            TraceItem(item_id=f.result_id, item_key=f"k-{f.result_id}", kind=TraceKind.RESULT)
            for f in fog
        ),
    )
    full = TraceSet(
        items=items, links=trace.links, rules=trace.rules, results=(*trace.results, *fog)
    )
    assert by_id(run(full))[REQ].state is RequirementVerdictState.FAIL


def test_result_selection_is_recorded_and_policies_differ() -> None:
    old_bad = result("RES-OLD", value="0.90", day=0)
    new_good = result("RES-NEW", value="0.10", day=1)
    other = result("RES-DRY", value="0.99", condition="weather=dry", day=2)
    trace = mini([old_bad, new_good, other])
    latest = by_id(run(trace))["C-1"]
    assert latest.state is CriterionVerdictState.PASS_COMPUTED
    assert latest.selection is not None
    assert [item.result_id for item in latest.selection.chosen] == ["RES-NEW"]
    assert {(item.result_id, item.reason) for item in latest.selection.excluded} == {
        ("RES-OLD", "SUPERSEDED_BY_LATER_RESULT"),
        ("RES-DRY", "CONDITION_MISMATCH"),
    }
    strict = compute_verdicts(
        trace, SelectionPolicy(name=SelectionPolicyName.ALL_MUST_PASS), computed_at=T0
    )
    assert by_id(strict)["C-1"].state is CriterionVerdictState.FAIL_COMPUTED


# --- digests, history, currentness ---


def test_same_input_gives_the_same_digests_and_time_does_not_change_the_verdict() -> None:
    first, second = run(demo_set(2)), run(demo_set(2))
    assert [item.revision_digest for item in first] == [item.revision_digest for item in second]
    later = run(demo_set(2), at=T0 + timedelta(days=9))
    assert [item.verdict_digest for item in later] == [item.verdict_digest for item in first]


def test_adding_a_rain_result_changes_only_rain_and_the_requirement() -> None:
    one = run(demo_set(1))
    two = run(demo_set(2), previous=one, at=T0 + timedelta(days=1), trigger="RECOMPUTE")
    before, after = by_id(one), by_id(two)
    unchanged = {"SYN-C-DET-DRY", "SYN-C-FA-DRY", "SYN-C-DET-FOG", "SYN-C-FA-FOG"}
    for name in unchanged:
        assert after[name] is before[name]
    for name in ("SYN-C-DET-RAIN", "SYN-C-FA-RAIN", REQ):
        assert after[name].revision_digest != before[name].revision_digest
        assert after[name].parent_digest == before[name].revision_digest
    assert before["SYN-C-DET-RAIN"].parent_digest is None


def test_title_only_change_is_not_stale_but_a_rule_or_result_change_is() -> None:
    old = demo_set(2)
    items = tuple(
        item.model_copy(update={"title": item.title + " (renamed)", "fields": {"note": "x"}})
        for item in old.items
    )
    renamed = TraceSet(items=items, links=old.links, rules=old.rules, results=old.results)
    assert dependency_changes(old, renamed) == ()
    assert all(item.state is CurrentnessState.CURRENT for item in mark_stale(renamed, ()).values())
    stricter = [
        rule.model_copy(update={"threshold": Decimal("0.95"), "rule_revision": 2})
        if rule.criterion_id == "SYN-C-DET-DRY"
        else rule
        for rule in old.rules
    ]
    edited = TraceSet(items=old.items, links=old.links, rules=tuple(stricter), results=old.results)
    changes = dependency_changes(old, edited)
    stale = mark_stale(edited, changes)
    flagged = {
        key[1] for key, value in stale.items() if value.state is CurrentnessState.STALE_BASIS
    }
    assert flagged == {"SYN-C-DET-DRY", REQ}
    threshold = next(item for item in changes if item.field == "threshold")
    assert (
        threshold.before,
        threshold.after,
        threshold.before_revision,
        threshold.after_revision,
    ) == ("0.90", "0.95", 1, 2)


def test_a_changed_rule_makes_a_new_dry_revision_that_records_what_changed() -> None:
    old = demo_set(1)
    first = run(old)
    stricter = [
        rule.model_copy(update={"threshold": Decimal("0.99"), "rule_revision": 2})
        if rule.criterion_id == "SYN-C-DET-DRY"
        else rule
        for rule in old.rules
    ]
    new = TraceSet(items=old.items, links=old.links, rules=tuple(stricter), results=old.results)
    changes = dependency_changes(old, new)
    second = run(
        new, previous=first, changes=changes, trigger="RULE_EDIT", at=T0 + timedelta(days=1)
    )
    dry = by_id(second)["SYN-C-DET-DRY"]
    assert dry.state is CriterionVerdictState.FAIL_COMPUTED
    assert dry.cause.trigger == "RULE_EDIT"
    assert [(item.field, item.before, item.after) for item in dry.cause.changed] == [
        ("rule_revision", "1", "2"),
        ("threshold", "0.90", "0.99"),
    ]
    assert by_id(second)["SYN-C-FA-DRY"] is by_id(first)["SYN-C-FA-DRY"]


# --- stored behaviour ---


async def opened(tmp_path: Path):
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    records = RequestRecords(
        runtime.ledger,
        SqliteControlRecordStore(runtime.ledger.engine),
        SystemClock(),
        UuidIdGenerator(),
    )
    return runtime, VerificationTraceService(records)


@pytest.mark.asyncio
async def test_confirmation_is_separate_and_stays_on_the_old_revision(tmp_path: Path) -> None:
    runtime, service = await opened(tmp_path)
    try:
        digest = service.replace_trace_set("p", demo_set(1), None, "human:local-user")
        digest = service.recompute("p", digest, "human:local-user")
        _, stored = service.read("p")
        assert stored is not None
        dry_one = service.current_verdicts("p", stored)[(SubjectKind.CRITERION, "SYN-C-DET-DRY")]
        requirement_one = service.current_verdicts("p", stored)[(SubjectKind.REQUIREMENT, REQ)]
        assert requirement_one.cause.trigger == "INITIAL_COMPUTE"

        history_before = stored.history
        digest = service.confirm(
            "p", dry_one.revision_digest, "human:local-user", "checked the dry run", digest
        )
        digest = service.confirm(
            "p", requirement_one.revision_digest, "human:local-user", "ok for now", digest
        )
        _, confirmed = service.read("p")
        assert confirmed is not None and confirmed.history == history_before

        digest = service.replace_trace_set("p", demo_set(2), digest, "human:local-user")
        flagged = service.currentness("p")
        assert (
            flagged[(SubjectKind.CRITERION, "SYN-C-DET-RAIN")].state is CurrentnessState.STALE_BASIS
        )
        assert flagged[(SubjectKind.CRITERION, "SYN-C-DET-DRY")].state is CurrentnessState.CURRENT
        assert flagged[(SubjectKind.REQUIREMENT, REQ)].state is CurrentnessState.STALE_BASIS
        _, waiting = service.read("p")
        assert (
            waiting is not None and waiting.history == history_before
        )  # nothing recomputed on its own

        digest = service.recompute("p", digest, "human:local-user")
        _, after = service.read("p")
        assert after is not None
        current = service.current_verdicts("p", after)
        dry_two = current[(SubjectKind.CRITERION, "SYN-C-DET-DRY")]
        requirement_two = current[(SubjectKind.REQUIREMENT, REQ)]
        assert dry_two.revision_digest == dry_one.revision_digest
        assert len(after.confirmations_of(dry_two.revision_digest)) == 1
        assert requirement_two.revision_digest != requirement_one.revision_digest
        assert requirement_two.state is RequirementVerdictState.FAIL_WITH_INCOMPLETE_COVERAGE
        assert after.confirmations_of(requirement_two.revision_digest) == ()
        assert len(after.confirmations_of(requirement_one.revision_digest)) == 1
        old = after.history[subject_key(SubjectKind.REQUIREMENT, REQ)]
        assert requirement_one.revision_digest in old  # the old revision is still there
        assert service.revision("p", requirement_one.revision_digest) == requirement_one
        assert old[-1] == requirement_two.revision_digest and len(old) == 2
        assert after.pending_changes == () and all(
            item.state is CurrentnessState.CURRENT for item in service.currentness("p").values()
        )
        old_target = requirement_one.revision_digest
        with pytest.raises(TraceError, match="TRACE_CONFIRM_TARGET_NOT_CURRENT"):
            service.confirm("p", old_target, "human:local-user", "late", digest)
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_writes_need_the_digest_that_was_read_and_ids_survive_storage(tmp_path: Path) -> None:
    runtime, service = await opened(tmp_path)
    try:
        trace = mini([result(value="0.30", numerator=3, denominator=10)])
        renamed = TraceSet(
            items=tuple(
                item.model_copy(update={"item_id": "007"}) if item.item_id == "C-1" else item
                for item in trace.items
            ),
            links=tuple(link.model_copy(update={"from_id": "007"}) for link in trace.links),
            rules=tuple(rule.model_copy(update={"criterion_id": "007"}) for rule in trace.rules),
            results=tuple(
                item.model_copy(update={"criterion_id": "007"}) for item in trace.results
            ),
        )
        digest = service.replace_trace_set("p", renamed, None, "human:local-user")
        with pytest.raises(TraceConflict):
            service.replace_trace_set("p", renamed, None, "human:local-user")
        with pytest.raises(TraceConflict):
            service.recompute("p", "0" * 64, "human:local-user")
        with pytest.raises(TraceError, match="TRACE_NOT_FOUND"):
            service.recompute("q", None, "human:local-user")
        digest = service.recompute("p", digest, "human:local-user")
        _, stored = service.read("p")
        assert stored is not None
        assert stored.trace_set.set_digest == renamed.set_digest
        assert {item.item_id for item in stored.trace_set.items} >= {"007"}
        assert stored.trace_set.rules[0].threshold == Decimal("0.50")
        assert str(stored.trace_set.results[0].value) == "0.30"
        assert (
            service.current_verdicts("p", stored)[(SubjectKind.CRITERION, "007")].state
            is CriterionVerdictState.PASS_COMPUTED
        )
        assert all(
            service.revision("p", digest).verdict_digest
            for digests in stored.history.values()
            for digest in digests
        )
    finally:
        runtime.close()


# --- the set itself ---


def test_the_set_rejects_broken_graphs_and_non_finite_numbers() -> None:
    good = mini([result()])
    with pytest.raises(ValueError, match="TRACE_LINK_ENDPOINT_INVALID"):
        TraceSet(
            items=good.items,
            links=(
                TraceLink(
                    link_id="x", from_id="REQ-1", to_id="C-1", relation=TraceRelation.REFINES
                ),
            ),
        )
    with pytest.raises(ValueError, match="TRACE_ITEM_ID_DUPLICATE"):
        TraceSet(items=(*good.items, good.items[0].model_copy(update={"item_key": "other"})))
    with pytest.raises(ValueError, match="TRACE_RESULT_ITEM_MISSING"):
        TraceSet(items=good.items[:2], results=good.results)
    with pytest.raises(ValueError):
        CriterionRule(
            rule_id="r",
            criterion_id="C-1",
            measure="m",
            comparator=Comparator.AT_LEAST,
            threshold=Decimal("NaN"),
            unit="u",
            condition="c",
        )
    with pytest.raises(ValueError):
        TraceItem(item_id=" 007", item_key="k", kind=TraceKind.REQUIREMENT)
    shuffled = TraceSet(
        items=tuple(reversed(good.items)), links=good.links, rules=good.rules, results=good.results
    )
    assert shuffled.set_digest == good.set_digest


def test_a_tampered_verdict_does_not_validate() -> None:
    verdict = run(demo_set(1))[0]
    payload = verdict.model_dump(mode="python")
    payload["state"] = (
        CriterionVerdictState.PASS_COMPUTED
        if verdict.state is not CriterionVerdictState.PASS_COMPUTED
        else CriterionVerdictState.FAIL_COMPUTED
    )
    with pytest.raises(ValueError, match="TRACE_VERDICT_DIGEST_MISMATCH"):
        VerdictRevision.model_validate(payload)


@pytest.mark.asyncio
async def test_each_revision_is_its_own_record_and_the_trace_record_only_lists_digests(
    tmp_path: Path,
) -> None:
    runtime, service = await opened(tmp_path)
    try:
        digest = service.replace_trace_set("p", demo_set(1), None, "human:local-user")
        service.recompute("p", digest, "human:local-user")
        _, stored = service.read("p")
        assert stored is not None
        digests = {d for ds in stored.history.values() for d in ds}
        assert len(digests) == 7  # six criteria and the requirement
        for one in digests:
            assert service.revision("p", one).revision_digest == one
        heads = runtime.ledger.read_heads("p")
        assert {f"THREAD:trace-verdict:{d}" for d in digests} <= set(heads)
        content = json.dumps(stored.model_dump(mode="json"))
        assert "THRESHOLD_NOT_MET" not in content and "NO_RESULT" not in content  # no verdict text
        page = service.history("p", stored, SubjectKind.CRITERION, "SYN-C-DET-RAIN", limit=5)
        assert page.total == 1 and len(page.revisions) == 1 and page.next_before is None
        with pytest.raises(TraceError, match="TRACE_HISTORY_CURSOR_UNKNOWN"):
            service.history("p", stored, SubjectKind.CRITERION, "SYN-C-DET-RAIN", before="0" * 64)
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_failure_while_saving_the_revisions_leaves_the_trace_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    records = RequestRecords(
        runtime.ledger,
        SqliteControlRecordStore(runtime.ledger.engine),
        SystemClock(),
        UuidIdGenerator(),
    )
    service = VerificationTraceService(records)
    try:
        digest = service.replace_trace_set("p", demo_set(1), None, "human:local-user")
        before = runtime.ledger.read_heads("p")
        stage = records.stage
        calls = {"count": 0}

        def failing(*args: Any, **kwargs: Any) -> Any:
            calls["count"] += 1
            if calls["count"] == 3:
                raise RuntimeError("the third record could not be prepared")
            return stage(*args, **kwargs)

        monkeypatch.setattr(records, "stage", failing)
        with pytest.raises(RuntimeError):
            service.recompute("p", digest, "human:local-user")
        monkeypatch.undo()
        assert runtime.ledger.read_heads("p") == before
        _, stored = service.read("p")
        assert stored is not None and stored.history == {}
    finally:
        runtime.close()
