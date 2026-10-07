"""A person marks hypotheses of different investigations as the same, and takes the mark back.

Asking about the same trace row again makes new hypothesis ids. A mark is only a reference: it
widens when a lesson counts as refuted and shows the earlier result beside the new hypothesis, and
it never merges the elimination, the standing or the order of tests. No model is called.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from tests.integration.test_hypothesis_link import investigate
from tests.integration.test_judgment_records import listed, record, stored
from tests.integration.test_research_request_v2 import ControlledResearchModel
from tests.integration.test_trace_origin import RAIN, Rpc, opened

from thoth.application.services.hypothesis_same import KEY as SAME_KEY
from thoth.application.services.hypothesis_same import HypothesisSame
from thoth.application.services.request_records import RequestRecords


async def ids(rpc: Rpc) -> list[str]:
    found = (await rpc("hypothesis/list", project_id="p"))["hypotheses"]
    return [item["hypothesis_id"] for item in found]


async def new_one(runtime: Any, rpc: Rpc, row: str | None) -> str:
    before = set(await ids(rpc))
    await investigate(runtime, rpc, row)
    (fresh,) = set(await ids(rpc)) - before
    return fresh


async def mark(rpc: Rpc, first: str, second: str, action: str = "LINK", **extra: Any) -> Any:
    return await rpc(
        "hypothesis/same/record",
        project_id="p",
        hypothesis_ids=[first, second],
        action=action,
        **extra,
    )


async def lessons(rpc: Rpc) -> list[dict[str, Any]]:
    return (await rpc("trace/lesson/list", project_id="p"))["lessons"]


def state_of(found: list[dict[str, Any]], hypothesis_id: str, outcome: str) -> str:
    (item,) = [
        x
        for x in found
        if x["hypothesis_id"] == hypothesis_id
        and x["kind"] == "TEST_RESULT"
        and x["outcome"] == outcome
    ]
    return str(item["state"])


@pytest.mark.asyncio
async def test_two_investigations_of_one_row_are_marked_the_same_and_the_mark_can_be_taken_back(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel(one=True, tested=True))
    try:
        first = await new_one(runtime, rpc, RAIN)
        second = await new_one(runtime, rpc, RAIN)
        assert first != second
        assert (await rpc("hypothesis/same/list", project_id="p"))["groups"] == []
        done = await mark(rpc, second, first, note="같은 줄을 다시 조사함")  # order does not matter
        assert done["groups"] == [sorted([first, second])]
        assert done["event"]["hypothesis_ids"] == sorted([first, second])
        assert done["event"]["actor_id"] == "human:local-user" and done["event"]["note"]
        member = done["members"][first]
        assert member["subject_id"] == RAIN and member["statement"] and member["investigated_at"]
        read = await rpc("hypothesis/same/list", project_id="p")
        assert read["groups"] == done["groups"] and len(read["events"]) == 1
        # taking it back is another event; nothing is removed
        ended = await mark(rpc, first, second, "UNLINK")
        assert ended["groups"] == [] and len(ended["events"]) == 2
        assert [e["action"] for e in stored(runtime, SAME_KEY)["events"]] == ["LINK", "UNLINK"]
        again = await mark(rpc, first, second)
        assert again["groups"] == [sorted([first, second])]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_mark_is_refused_with_its_reason_and_a_refusal_writes_nothing(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel(one=True, tested=True))
    try:
        first = await new_one(runtime, rpc, RAIN)
        second = await new_one(runtime, rpc, RAIN)
        plain = await new_one(runtime, rpc, None)  # an ordinary question: no trace row behind it
        refused = {
            "SAME_IDENTICAL": (first, first, "LINK"),
            "SAME_NOT_FOUND": (first, "hypothesis:none", "LINK"),
            "SAME_NOT_INVESTIGATION": (first, plain, "LINK"),
            "SAME_NOT_LINKED": (first, second, "UNLINK"),
        }
        for reason, (a, b, action) in refused.items():
            message = await rpc.error(
                "hypothesis/same/record", project_id="p", hypothesis_ids=[a, b], action=action
            )
            assert reason in message, (reason, message)
        await mark(rpc, first, second)
        message = await rpc.error(
            "hypothesis/same/record", project_id="p", hypothesis_ids=[second, first], action="LINK"
        )
        assert "SAME_ALREADY_LINKED" in message
        assert (
            await rpc.error(
                "hypothesis/same/record", project_id="p", hypothesis_ids=[first], action="LINK"
            )
            == "invalid method parameters"
        )
        assert len((await rpc("hypothesis/same/list", project_id="p"))["events"]) == 1
        service = HypothesisSame(cast(RequestRecords, SimpleNamespace(ledger=runtime.ledger)))
        for actor in ("model:gpt", "agent:research", "system:verification-trace", ""):
            with pytest.raises(PermissionError, match="RECORD_HUMAN_ONLY"):
                service.record(
                    project_id="p",
                    hypothesis_ids=(first, second),
                    action="UNLINK",
                    note="",
                    actor_id=actor,
                )
        assert len((await rpc("hypothesis/same/list", project_id="p"))["events"]) == 1
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_three_investigations_make_one_group_and_one_unlink_can_split_it(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel(one=True, tested=True))
    try:
        a = await new_one(runtime, rpc, RAIN)
        b = await new_one(runtime, rpc, RAIN)
        c = await new_one(runtime, rpc, RAIN)
        await mark(rpc, a, b)
        grouped = await mark(rpc, b, c)
        assert grouped["groups"] == [sorted([a, b, c])]
        # the marks themselves are the pairs; a and c are in one group without a mark between them
        assert sorted(map(sorted, grouped["pairs"])) == sorted([sorted([a, b]), sorted([b, c])])
        split = await mark(rpc, b, c, "UNLINK")
        assert split["groups"] == [sorted([a, b])]
        assert split["pairs"] == [sorted([a, b])]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_lesson_is_refuted_across_a_marked_group_only_and_counts_stay_per_hypothesis(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel(one=True, tested=True))
    try:
        old = await new_one(runtime, rpc, RAIN)
        new = await new_one(runtime, rpc, RAIN)
        other = await new_one(runtime, rpc, RAIN)  # never marked
        await record(rpc, old, "test-1", "THIS_HYPOTHESIS")
        await record(rpc, other, "test-1", "THIS_HYPOTHESIS")
        await record(rpc, new, "test-1", "ALTERNATIVE")  # a later, opposite result of the new one
        # no mark yet: three separate hypotheses, so nothing is refuted
        found = await lessons(rpc)
        assert state_of(found, old, "THIS_HYPOTHESIS") == "SAME_CONDITION"
        assert not any("same_hypothesis_ids" in item for item in found)
        await mark(rpc, old, new)
        found = await lessons(rpc)
        assert state_of(found, old, "THIS_HYPOTHESIS") == "REFUTED"  # the group says the opposite
        assert state_of(found, other, "THIS_HYPOTHESIS") == "SAME_CONDITION"  # outside the group
        assert state_of(found, new, "ALTERNATIVE") == "SAME_CONDITION"
        marked = {
            item["hypothesis_id"]: item["same_hypothesis_ids"]
            for item in found
            if "same_hypothesis_ids" in item
        }
        assert marked == {old: [new], new: [old]}
        # counts, elimination and standing are read per hypothesis id, never added across the group
        items = {item["hypothesis_id"]: item for item in await listed(rpc)}
        assert (items[old]["elimination"], items[old]["standing"]) == (None, "FITS")
        assert (items[new]["elimination"], items[new]["standing"]) == ("SINGLE", "AGAINST_ONCE")
        # taking the mark back puts the lesson as it was
        await mark(rpc, old, new, "UNLINK")
        found = await lessons(rpc)
        assert state_of(found, old, "THIS_HYPOTHESIS") == "SAME_CONDITION"
        assert not any("same_hypothesis_ids" in item for item in found)
        # with the mark again and a second result against the old one, each keeps its own count
        await mark(rpc, old, new)
        await record(rpc, old, "test-2", "ALTERNATIVE")
        items = {item["hypothesis_id"]: item for item in await listed(rpc)}
        assert items[old]["elimination"] == "SINGLE" and items[new]["elimination"] == "SINGLE"
        assert (items[old]["standing"], items[new]["standing"]) == ("MIXED", "AGAINST_ONCE")
    finally:
        runtime.close()
