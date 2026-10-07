"""Lessons: what a rule computed or a person recorded, recalled only in the same condition.

A lesson is added with the record it rests on and is a reference, never a sentence. It is recalled
for a row only when the criterion, the unit, the condition and the rule revision all match; it goes
stale when its record changed and is refuted by a later opposite result, and it is never removed.
No model is called. These tests go through the public methods.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_hypothesis_link import change_result, investigate
from tests.integration.test_judgment_records import (
    close,
    only_hypothesis,
    record,
    stored,
)
from tests.integration.test_research_request_v2 import ControlledResearchModel
from tests.integration.test_trace_origin import RAIN, Rpc, opened
from tests.integration.trace_demo_helpers import demo_set, with_notice

from thoth.application.services.lesson_ledger import KEY as LESSON_KEY
from thoth.application.services.trace_csv import export_rows, write_csv

OBSERVED = "the rain run matched the dry run"


async def lessons(rpc: Rpc) -> dict[str, dict[str, Any]]:
    found = (await rpc("trace/lesson/list", project_id="p"))["lessons"]
    return {item["lesson_id"]: item for item in found}


def by(
    found: dict[str, dict[str, Any]], kind: str, outcome: str | None = None
) -> list[dict[str, Any]]:
    return [
        item
        for item in found.values()
        if item["kind"] == kind and (outcome is None or item["outcome"] == outcome)
    ]


async def change_rule(rpc: Rpc, rule_id: str, threshold: str) -> None:
    """A new revision of one rule, as a person would import it."""
    current = (await rpc("trace/read", project_id="p"))["set_digest"]
    rows = []
    for row in export_rows(with_notice(demo_set(2))):
        row = {**row, "base_set_digest": current}
        if row["row_type"] == "RULE" and row["rule_id"] == rule_id:
            row = {
                **row,
                "rule_revision": str(int(row["rule_revision"]) + 1),
                "threshold": threshold,
            }
        rows.append(row)
    await rpc.load(write_csv(rows), "UPDATE")


@pytest.mark.asyncio
async def test_a_recorded_result_is_a_lesson_of_its_rows_condition_and_a_reference_not_a_sentence(
    tmp_path: Path,
) -> None:
    model = ControlledResearchModel(one=True, tested=True)
    runtime, rpc = await opened(tmp_path, model)
    try:
        await investigate(runtime, rpc, RAIN)
        hypothesis_id = await only_hypothesis(rpc)
        calls = len(model.calls)
        assert await lessons(rpc) == {}
        await record(rpc, hypothesis_id, "test-1", "ALTERNATIVE", evidence_refs=["doc://run-7"])
        found = await lessons(rpc)
        (result,) = by(found, "TEST_RESULT")
        (single,) = by(found, "ELIMINATION", "SINGLE")
        assert (result["outcome"], result["state"], result["subject_id"]) == (
            "ALTERNATIVE",
            "SAME_CONDITION",
            RAIN,
        )
        assert (
            result["detail"]["observations"][0]["observation"] == OBSERVED
        )  # read back, not copied
        assert single["detail"]["observations"][0]["evidence_refs"] == ["doc://run-7"]
        await record(rpc, hypothesis_id, "test-2", "ALTERNATIVE")
        found = await lessons(rpc)
        assert len(by(found, "TEST_RESULT")) == 2
        assert {item["state"] for item in by(found, "ELIMINATION")} == {"STALE", "SAME_CONDITION"}
        assert [item["outcome"] for item in by(found, "ELIMINATION", "REPEATED")] == ["REPEATED"]
        # what is stored names records and a context; the sentence stays in the record it came from
        refs = stored(runtime, LESSON_KEY)["refs"]
        assert len(refs) == 4 and OBSERVED not in str(refs)
        assert {r["context"]["condition"] for r in refs} == {"weather=rain"}
        assert len(model.calls) == calls  # no model was asked
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_result_that_is_replaced_makes_its_lesson_stale_and_an_opposite_one_refutes(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel(one=True, tested=True))
    try:
        await investigate(runtime, rpc, RAIN)
        hypothesis_id = await only_hypothesis(rpc)
        await record(rpc, hypothesis_id, "test-1", "ALTERNATIVE")
        await record(rpc, hypothesis_id, "test-1", "THIS_HYPOTHESIS")  # the same test, corrected
        found = await lessons(rpc)
        states = {(i["kind"], i["outcome"]): i["state"] for i in found.values()}
        assert states[("TEST_RESULT", "ALTERNATIVE")] == "STALE"  # its record was replaced
        assert states[("ELIMINATION", "SINGLE")] == "STALE"  # the elimination no longer holds
        assert states[("TEST_RESULT", "THIS_HYPOTHESIS")] == "SAME_CONDITION"
        # a later, different test says the opposite of an earlier one: the earlier is refuted
        await record(rpc, hypothesis_id, "test-2", "ALTERNATIVE")
        later = {
            (i["test_id"], i["outcome"]): i["state"] for i in by(await lessons(rpc), "TEST_RESULT")
        }
        assert later[("test-1", "THIS_HYPOTHESIS")] == "REFUTED"
        assert later[("test-2", "ALTERNATIVE")] == "SAME_CONDITION"
        assert len(stored(runtime, LESSON_KEY)["refs"]) == 5  # nothing was removed
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_lesson_is_not_recalled_once_the_rule_revision_differs_but_stays_on_record(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel(one=True, tested=True))
    try:
        await investigate(runtime, rpc, RAIN)
        hypothesis_id = await only_hypothesis(rpc)
        await record(rpc, hypothesis_id, "test-1", "ALTERNATIVE")
        assert len(await lessons(rpc)) == 2
        await change_rule(rpc, "R-SYN-C-DET-RAIN", "0.80")  # the same row under a new rule revision
        assert await lessons(rpc) == {}  # another condition: not recalled, not even as stale
        assert len(stored(runtime, LESSON_KEY)["refs"]) == 2
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_result_with_no_trace_row_behind_it_makes_no_lesson(tmp_path: Path) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel(one=True, tested=True))
    try:
        await investigate(runtime, rpc, None)  # an ordinary question: no row, no context
        await record(rpc, await only_hypothesis(rpc), "test-1", "ALTERNATIVE")
        assert await lessons(rpc) == {}
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_recorded_closure_is_a_lesson_and_a_fix_only_once_its_effect_is_confirmed(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel())
    try:
        await rpc.rain_arrives()
        await close(rpc, RAIN, "HUMAN_CLOSED", basis_ref="minutes-2026-10-06")
        await close(rpc, RAIN, "FIX_APPLIED")  # a fix with no confirmed effect is not a lesson
        found = await lessons(rpc)
        assert [(i["kind"], i["outcome"], i["state"]) for i in found.values()] == [
            ("HUMAN_CLOSURE", "HUMAN_CLOSED", "SAME_CONDITION")
        ]
        assert next(iter(found.values()))["detail"]["basis_ref"] == "minutes-2026-10-06"
        # new results that meet the row confirm the effect: its lesson appears after the import
        await change_result(rpc, "SYN-RES-SYN-C-DET-RAIN", phase=2, value="0.95", numerator="19")
        found = await lessons(rpc)
        (effect,) = by(found, "EFFECT_CONFIRMED")
        assert effect["state"] == "SAME_CONDITION" and effect["actor_id"] == "system:lesson-rule"
        (closure,) = by(found, "HUMAN_CLOSURE")
        assert closure["state"] == "STALE"  # the verdict it was recorded against has changed
        assert len(stored(runtime, LESSON_KEY)["refs"]) == 2
    finally:
        runtime.close()
