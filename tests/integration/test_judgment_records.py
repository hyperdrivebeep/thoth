"""A person's records about test results, refutation conditions and closed trace rows.

They are events added beside what the model and the rules produced: the hypothesis is not edited,
a verdict is never overwritten, nothing is deleted, and no model is called. These tests go through
the public methods; the human-only rule is checked on the services, which take the actor.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from tests.integration.test_hypothesis_link import change_result, investigate, verdict_state
from tests.integration.test_research_request_v2 import ControlledResearchModel
from tests.integration.test_trace_origin import DRY, FOG, RAIN, Rpc, opened

from thoth.application.services.discrimination_ledger import KEY as DISCRIMINATION_KEY
from thoth.application.services.discrimination_ledger import DiscriminationLedger
from thoth.application.services.hypothesis_link_view import HypothesisLinkReader
from thoth.application.services.request_records import RequestRecords
from thoth.application.services.trace_closure import KEY as CLOSURE_KEY
from thoth.application.services.trace_closure import TraceClosures
from thoth.domain.enums import EntityType
from thoth.domain.verification_trace import SubjectKind

OBSERVED = "the rain run matched the dry run"
NOT_PEOPLE = ("model:gpt", "agent:research", "system:verification-trace", "")


async def only_hypothesis(rpc: Rpc) -> str:
    (found,) = (await rpc("hypothesis/list", project_id="p"))["hypotheses"]
    return found["hypothesis_id"]


async def record(
    rpc: Rpc, hypothesis_id: str, test_id: str, matched: str, **extra: Any
) -> dict[str, Any]:
    return await rpc(
        "hypothesis/test/result/record",
        project_id="p",
        hypothesis_id=hypothesis_id,
        test_id=test_id,
        observation=extra.pop("observation", OBSERVED),
        matched=matched,
        **extra,
    )


async def listed(rpc: Rpc) -> list[dict[str, Any]]:
    return (await rpc("hypothesis/test/result/list", project_id="p"))["items"]


def stored(runtime: Any, key: str) -> dict[str, Any]:
    content = HypothesisLinkReader(runtime.ledger).content("p", EntityType.THREAD, key)
    assert content is not None
    return content


@pytest.mark.asyncio
async def test_a_result_that_fits_the_alternative_eliminates_once_and_two_tests_make_it_repeated(
    tmp_path: Path,
) -> None:
    model = ControlledResearchModel(one=True, tested=True)
    runtime, rpc = await opened(tmp_path, model)
    try:
        await investigate(runtime, rpc, None)
        hypothesis_id = await only_hypothesis(rpc)
        digest = (await rpc("hypothesis/read", project_id="p", hypothesis_id=hypothesis_id))[
            "hypothesis"
        ]["revision_digest"]
        calls = len(model.calls)
        assert await listed(rpc) == []  # nothing recorded yet: nothing to show
        first = await record(
            rpc, hypothesis_id, "test-1", "ALTERNATIVE", evidence_refs=["doc://run-7", "  "]
        )
        (item,) = first["items"]
        assert (item["elimination"], item["result_history_count"]) == ("SINGLE", 1)
        assert item["results"][0]["evidence_refs"] == ["doc://run-7"]
        assert first["result"]["actor_id"] == "human:local-user"
        # a corrected result replaces the earlier one in what counts, and the earlier one stays
        (item,) = (await record(rpc, hypothesis_id, "test-1", "THIS_HYPOTHESIS"))["items"]
        assert (item["elimination"], item["result_history_count"]) == (None, 2)
        assert [r["matched"] for r in item["results"]] == ["THIS_HYPOTHESIS"]
        await record(rpc, hypothesis_id, "test-1", "ALTERNATIVE")
        (item,) = (await record(rpc, hypothesis_id, "test-2", "ALTERNATIVE"))["items"]
        assert (item["elimination"], item["result_history_count"]) == ("REPEATED", 4)
        assert [e["matched"] for e in stored(runtime, DISCRIMINATION_KEY)["results"]] == [
            "ALTERNATIVE",
            "THIS_HYPOTHESIS",
            "ALTERNATIVE",
            "ALTERNATIVE",
        ]
        assert len(model.calls) == calls  # a person wrote this; no model was asked
        after = await rpc("hypothesis/read", project_id="p", hypothesis_id=hypothesis_id)
        assert after["hypothesis"]["revision_digest"] == digest  # the hypothesis is not edited
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_result_needs_a_real_hypothesis_test_and_words_and_a_refused_one_writes_nothing(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel(one=True, tested=True))
    try:
        await investigate(runtime, rpc, None)
        hypothesis_id = await only_hypothesis(rpc)
        refused = {
            "TEST_NOT_FOUND": (hypothesis_id, "nope", OBSERVED, "NEITHER"),
            "HYPOTHESIS_NOT_FOUND": ("hypothesis:none", "test-1", OBSERVED, "NEITHER"),
            "OBSERVATION_REQUIRED": (hypothesis_id, "test-1", "   ", "NEITHER"),
        }
        for reason, (hid, test, words, matched) in refused.items():
            message = await rpc.error(
                "hypothesis/test/result/record",
                project_id="p",
                hypothesis_id=hid,
                test_id=test,
                observation=words,
                matched=matched,
            )
            assert reason in message
        assert (
            await rpc.error(
                "hypothesis/test/result/record",
                project_id="p",
                hypothesis_id=hypothesis_id,
                test_id="test-1",
                observation=OBSERVED,
                matched="MAYBE",
            )
            == "invalid method parameters"
        )
        assert await listed(rpc) == []
        ledger = DiscriminationLedger(cast(RequestRecords, SimpleNamespace(ledger=runtime.ledger)))
        for actor in NOT_PEOPLE:
            with pytest.raises(PermissionError, match="RECORD_HUMAN_ONLY"):
                ledger.record_result(
                    project_id="p",
                    hypothesis_id=hypothesis_id,
                    test_id="test-1",
                    observation=OBSERVED,
                    matched="NEITHER",
                    evidence_refs=(),
                    actor_id=actor,
                )
            with pytest.raises(PermissionError, match="RECORD_HUMAN_ONLY"):
                ledger.record_conditions(
                    project_id="p", hypothesis_id=hypothesis_id, conditions=("x",), actor_id=actor
                )
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_refutation_conditions_are_a_list_a_person_writes_and_each_write_is_kept(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel(one=True, tested=True))
    try:
        await investigate(runtime, rpc, None)
        hypothesis_id = await only_hypothesis(rpc)

        async def write(*conditions: str) -> dict[str, Any]:
            done = await rpc(
                "hypothesis/refutation/record",
                project_id="p",
                hypothesis_id=hypothesis_id,
                conditions=list(conditions),
            )
            return done["items"][0]

        item = await write("the rain run is no worse than the dry run", "the other sensor agrees")
        assert item["refutation_conditions"] == [
            "the rain run is no worse than the dry run",
            "the other sensor agrees",
        ]
        item = await write("only the first")
        assert (item["refutation_conditions"], item["conditions_history_count"]) == (
            ["only the first"],
            2,
        )
        item = await write()  # clearing is a record too
        assert (item["refutation_conditions"], item["conditions_history_count"]) == ([], 3)
        assert len(stored(runtime, DISCRIMINATION_KEY)["conditions"]) == 3
        assert "CONDITION_BLANK" in await rpc.error(
            "hypothesis/refutation/record",
            project_id="p",
            hypothesis_id=hypothesis_id,
            conditions=["fine", "  "],
        )
        assert "HYPOTHESIS_NOT_FOUND" in await rpc.error(
            "hypothesis/refutation/record",
            project_id="p",
            hypothesis_id="hypothesis:none",
            conditions=["x"],
        )
        assert (
            await rpc.error(
                "hypothesis/refutation/record",
                project_id="p",
                hypothesis_id=hypothesis_id,
                conditions=[f"c{i}" for i in range(11)],
            )
            == "invalid method parameters"
        )
        assert len(stored(runtime, DISCRIMINATION_KEY)["conditions"]) == 3  # refusals wrote nothing
    finally:
        runtime.close()


async def verdict_of(rpc: Rpc, subject_id: str) -> dict[str, Any]:
    view = await rpc("trace/read", project_id="p")
    return next(item for item in view["verdicts"] if item["subject_id"] == subject_id)


async def close(rpc: Rpc, subject_id: str, kind: str, **extra: Any) -> dict[str, Any]:
    params: dict[str, Any] = dict(
        project_id="p",
        subject_kind="CRITERION",
        subject_id=subject_id,
        kind=kind,
        basis_ref="ECN-12",
        current_verdict_revision=(await verdict_of(rpc, subject_id))["revision_digest"],
    )
    return await rpc("trace/closure/record", **{**params, **extra})


async def closures(rpc: Rpc) -> dict[str, dict[str, Any]]:
    found = (await rpc("trace/closure/list", project_id="p"))["closures"]
    return {item["subject_id"]: item for item in found}


@pytest.mark.asyncio
async def test_a_person_records_a_closure_with_its_document_and_the_verdict_is_not_touched(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel())
    try:
        assert await closures(rpc) == {}
        before = await verdict_of(rpc, FOG)
        done = await close(rpc, FOG, "FIX_APPLIED", note="wiring replaced")
        (row,) = done["closures"]
        assert (row["subject_id"], row["current_state"]) == (FOG, "HOLD_NO_RESULT")
        assert (row["effect_confirmed"], row["verdict_changed_since"]) == (False, False)
        (event,) = row["events"]
        assert (event["kind"], event["basis_ref"], event["verdict_state"]) == (
            "FIX_APPLIED",
            "ECN-12",
            "HOLD_NO_RESULT",
        )
        assert event["actor_id"] == "human:local-user"
        after = await verdict_of(rpc, FOG)  # the rule's own word is exactly what it was
        assert (after["revision_digest"], after["state"]) == (
            before["revision_digest"],
            before["state"],
        )
        # the other three kinds are recorded too, in order; only a changed condition has a scope
        await close(rpc, FOG, "HUMAN_CLOSED", basis_ref="minutes-2026-10-06")
        await close(rpc, FOG, "WAIVER_RECORDED", basis_ref="deviation DEV-4")
        last = await close(rpc, FOG, "CONDITION_CHANGED", scope="fog below 50 m", basis_ref="OPS-9")
        events = last["closures"][0]["events"]
        assert [e["kind"] for e in events] == [
            "FIX_APPLIED",
            "HUMAN_CLOSED",
            "WAIVER_RECORDED",
            "CONDITION_CHANGED",
        ]
        assert [e["scope"] for e in events] == [None, None, None, "fog below 50 m"]
        assert len(stored(runtime, CLOSURE_KEY)["events"]) == 4
        assert (await verdict_of(rpc, FOG))["revision_digest"] == before["revision_digest"]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_closure_needs_its_document_a_row_that_is_not_met_and_a_verdict_still_current(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel())
    try:
        revision = (await verdict_of(rpc, FOG))["revision_digest"]
        base: dict[str, Any] = dict(
            project_id="p",
            subject_kind="CRITERION",
            subject_id=FOG,
            kind="FIX_APPLIED",
            basis_ref="ECN-12",
            current_verdict_revision=revision,
        )
        refused = {
            "CLOSURE_BASIS_REQUIRED": dict(basis_ref="   "),
            "CLOSURE_SCOPE_REQUIRED": dict(kind="CONDITION_CHANGED"),
            "CLOSURE_ROW_NOT_FOUND": dict(subject_id="NO-SUCH-ROW"),
            "CLOSURE_ROW_ALREADY_MET": dict(
                subject_id=DRY,
                current_verdict_revision=(await verdict_of(rpc, DRY))["revision_digest"],
            ),
            "CLOSURE_VERDICT_CHANGED_AGAIN": dict(current_verdict_revision="0" * 64),
        }
        for reason, patch in refused.items():
            assert reason in await rpc.error("trace/closure/record", **{**base, **patch})
        for patch in (dict(basis_ref=""), dict(kind="EFFECT_CONFIRMED"), dict(kind="WAIVED")):
            assert (
                await rpc.error("trace/closure/record", **{**base, **patch})
                == "invalid method parameters"
            )
        assert await closures(rpc) == {}  # nothing was written
        checker = TraceClosures(cast(RequestRecords, SimpleNamespace(ledger=runtime.ledger)))
        for actor in NOT_PEOPLE:
            with pytest.raises(PermissionError, match="RECORD_HUMAN_ONLY"):
                checker.record(
                    project_id="p",
                    subject_kind=SubjectKind.CRITERION,
                    subject_id=FOG,
                    kind="FIX_APPLIED",
                    basis_ref="ECN-12",
                    note="",
                    scope=None,
                    current_verdict_revision=revision,
                    actor_id=actor,
                )
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_the_effect_is_confirmed_only_by_a_met_verdict_on_new_results_after_the_fix(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel())
    try:
        await rpc.rain_arrives()  # the rain row goes from held to failed: a change before any fix
        assert await verdict_state(rpc, RAIN) == "FAIL_COMPUTED"
        await close(rpc, RAIN, "FIX_APPLIED")
        (row,) = (await closures(rpc)).values()
        assert row["effect_confirmed"] is False  # that change was before the fix
        # new results arrive after the fix and the row is met
        await change_result(rpc, "SYN-RES-SYN-C-DET-RAIN", phase=2, value="0.95", numerator="19")
        assert await verdict_state(rpc, RAIN) == "PASS_COMPUTED"
        row = (await closures(rpc))[RAIN]
        assert row["effect_confirmed"] is True
        assert (row["current_state"], row["verdict_changed_since"]) == ("PASS_COMPUTED", True)
        assert row["events"][0]["verdict_state"] == "FAIL_COMPUTED"  # what it was when recorded
        assert row["effect_verdict_revision"] == (await verdict_of(rpc, RAIN))["revision_digest"]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_waiver_on_record_leaves_the_failed_verdict_as_it_was(tmp_path: Path) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel())
    try:
        await rpc.rain_arrives()
        before = await verdict_of(rpc, RAIN)
        await close(rpc, RAIN, "WAIVER_RECORDED", basis_ref="deviation DEV-4")
        row = (await closures(rpc))[RAIN]
        assert (row["effect_confirmed"], row["current_state"]) == (False, "FAIL_COMPUTED")
        after = await verdict_of(rpc, RAIN)
        assert (after["state"], after["revision_digest"]) == (
            "FAIL_COMPUTED",
            before["revision_digest"],
        )  # the original not-met verdict is untouched
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_the_listed_standing_follows_the_results_and_the_sealed_appraisal_is_not_touched(
    tmp_path: Path,
) -> None:
    model = ControlledResearchModel(one=True, tested=True)
    runtime, rpc = await opened(tmp_path, model)
    try:
        await investigate(runtime, rpc, None)
        hypothesis_id = await only_hypothesis(rpc)
        before = (await rpc("hypothesis/read", project_id="p", hypothesis_id=hypothesis_id))[
            "hypothesis"
        ]
        calls = len(model.calls)

        async def standing() -> str:
            (item,) = await listed(rpc)
            return str(item["standing"])

        await record(rpc, hypothesis_id, "test-1", "NEITHER")
        assert await standing() == "NONE"
        await record(rpc, hypothesis_id, "test-1", "ALTERNATIVE")
        assert await standing() == "AGAINST_ONCE"
        await record(rpc, hypothesis_id, "test-2", "THIS_HYPOTHESIS")
        assert await standing() == "MIXED"
        await record(rpc, hypothesis_id, "test-2", "ALTERNATIVE")  # the latest result of test-2
        assert await standing() == "AGAINST_REPEATED"
        await record(rpc, hypothesis_id, "test-1", "THIS_HYPOTHESIS")  # corrected
        await record(rpc, hypothesis_id, "test-2", "THIS_HYPOTHESIS")
        assert await standing() == "FITS"
        # the sealed-test appraisal and the whole record are exactly what they were
        after = (await rpc("hypothesis/read", project_id="p", hypothesis_id=hypothesis_id))[
            "hypothesis"
        ]
        assert after["empirical_appraisal"] == before["empirical_appraisal"]
        assert after["revision_digest"] == before["revision_digest"]
        assert "standing" not in stored(runtime, DISCRIMINATION_KEY)  # read, never stored
        assert len(model.calls) == calls
    finally:
        runtime.close()
