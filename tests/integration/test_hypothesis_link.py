"""A hypothesis from a trace row remembers its verdict; a changed verdict makes it stale.

Stale hypotheses are not used for approval or execution until a person re-checks them. A person's
confirmation of a verdict is not a change. These tests go through the public methods.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from tests.integration.test_research_request_v2 import ControlledResearchModel
from tests.integration.test_trace_origin import DRY, RAIN, Rpc, opened, origin_for
from tests.integration.trace_demo_helpers import demo_set, with_notice

from thoth.application.services.hypothesis_link_recheck import (
    HypothesisLinkRechecks,
    RecheckRefused,
)
from thoth.application.services.hypothesis_link_view import HypothesisLinkReader
from thoth.application.services.request_records import RequestRecords
from thoth.application.services.research_freshness import ResearchFreshnessService
from thoth.application.services.trace_csv import export_rows, write_csv
from thoth.apps.runtime import AppRuntime

FA_RAIN = "SYN-C-FA-RAIN"


async def investigate(runtime: AppRuntime, rpc: Rpc, row: str | None) -> str:
    """Start a research thread (from a trace row when given) and return its thread id."""
    extra: dict[str, Any] = {} if row is None else {"origin": await origin_for(rpc, row)}
    started = await rpc(
        "thread/start",
        project_id="p",
        problem=f"{row or 'plain'} question",
        contract_version=2,
        **extra,
    )
    await runtime.bus.drain()
    return str(started["thread_id"])


async def links(rpc: Rpc) -> dict[str, dict[str, Any]]:
    listed = await rpc("hypothesis/link/list", project_id="p")
    return {item["hypothesis_id"]: item for item in listed["links"]}


async def change_result(rpc: Rpc, result_id: str, *, phase: int = 1, **changes: str) -> None:
    """Update one result row of the stored trace (a new result revision), as a person would."""
    current = (await rpc("trace/read", project_id="p"))["set_digest"]
    rows = []
    for row in export_rows(with_notice(demo_set(phase))):
        row = {**row, "base_set_digest": current}
        if row["row_type"] == "RESULT" and row["result_id"] == result_id:
            row = {**row, "result_revision": str(int(row["result_revision"]) + 1), **changes}
        rows.append(row)
    await rpc.load(write_csv(rows), "UPDATE")


async def verdict_state(rpc: Rpc, subject_id: str) -> str:
    view = await rpc("trace/read", project_id="p")
    return next(item["state"] for item in view["verdicts"] if item["subject_id"] == subject_id)


@pytest.mark.asyncio
async def test_a_hypothesis_from_a_row_is_linked_and_only_a_content_change_makes_it_stale(
    tmp_path: Path,
) -> None:
    model = ControlledResearchModel(one=True)
    runtime, rpc = await opened(tmp_path, model)
    try:
        await investigate(runtime, rpc, None)  # an ordinary question: its hypothesis has no link
        assert await links(rpc) == {}
        await investigate(runtime, rpc, RAIN)
        (item,) = (await links(rpc)).values()
        assert (item["subject_id"], item["state"], item["change"]) == (RAIN, "CURRENT", None)
        assert item["link_state"] == "HOLD_NO_RESULT"
        # a person's confirmation is stored apart from the verdict: it is not a change
        view = await rpc("trace/read", project_id="p")
        rain = next(v for v in view["verdicts"] if v["subject_id"] == RAIN)
        await rpc(
            "trace/confirm",
            project_id="p",
            verdict_revision_digest=rain["revision_digest"],
            rationale="looked at it",
            expected_digest=view["record_digest"],
        )
        assert next(iter((await links(rpc)).values()))["state"] == "CURRENT"
        # the rain results arrive: what the verdict says changed
        await rpc.rain_arrives()
        (stale,) = (await links(rpc)).values()
        assert (stale["state"], stale["change"]) == ("STALE", "CONTENT_CHANGED")
        assert (stale["link_state"], stale["current_state"]) == ("HOLD_NO_RESULT", "FAIL_COMPUTED")
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_met_row_that_turns_failed_is_told_apart_as_a_flip(tmp_path: Path) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel(one=True))
    try:
        await investigate(runtime, rpc, DRY)
        assert await verdict_state(rpc, DRY) == "PASS_COMPUTED"
        await change_result(rpc, "SYN-RES-SYN-C-DET-DRY", value="0.50", numerator="10")
        assert await verdict_state(rpc, DRY) == "FAIL_COMPUTED"
        (item,) = (await links(rpc)).values()
        assert (item["state"], item["change"]) == ("STALE", "FLIPPED")
        assert (item["link_state"], item["current_state"]) == ("PASS_COMPUTED", "FAIL_COMPUTED")
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_recheck_is_a_person_a_reason_and_an_event_and_never_overwrites(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel(one=True))
    try:
        first, second = await investigate(runtime, rpc, RAIN), await investigate(runtime, rpc, RAIN)
        other = await investigate(runtime, rpc, DRY)
        await investigate(runtime, rpc, FA_RAIN)
        await rpc.rain_arrives()
        found = await links(rpc)
        assert len(found) == 4
        rain_ids = [key for key, item in found.items() if item["subject_id"] == RAIN]
        dry_id = next(key for key, item in found.items() if item["subject_id"] == DRY)
        fa_id = next(key for key, item in found.items() if item["subject_id"] == FA_RAIN)
        assert first != second != other
        seen = found[rain_ids[0]]["current_verdict_revision"]
        refused = {
            "HYPOTHESIS_LINK_NOT_STALE": dict(
                hypothesis_ids=[dry_id],
                current_verdict_revision=found[dry_id]["current_verdict_revision"],
            ),
            "HYPOTHESIS_LINK_MISSING": dict(
                hypothesis_ids=["hypothesis:nothing"], current_verdict_revision=seen
            ),
            "HYPOTHESIS_LINKS_DIFFER": dict(
                hypothesis_ids=[*rain_ids, fa_id], current_verdict_revision=seen
            ),
            "LINK_VERDICT_CHANGED_AGAIN": dict(
                hypothesis_ids=rain_ids, current_verdict_revision="0" * 64
            ),
        }
        for reason, params in refused.items():
            assert reason in await rpc.error(
                "hypothesis/link/recheck", project_id="p", reason_code="UNRELATED", **params
            )
        assert all(
            item["state"] == "STALE" for key, item in (await links(rpc)).items() if key in rain_ids
        )
        # "other" needs words
        assert "RECHECK_NOTE_REQUIRED" in await rpc.error(
            "hypothesis/link/recheck",
            project_id="p",
            hypothesis_ids=rain_ids,
            reason_code="OTHER",
            current_verdict_revision=seen,
        )
        # a reason asking for more research is recorded and leaves the hypotheses stale
        done = await rpc(
            "hypothesis/link/recheck",
            project_id="p",
            hypothesis_ids=rain_ids,
            reason_code="NEEDS_RESEARCH",
            current_verdict_revision=seen,
        )
        assert (
            len(done["events"]) == 2 and len({event["batch_id"] for event in done["events"]}) == 1
        )
        assert all(
            item["state"] == "STALE" and item["recheck"]["reason_code"] == "NEEDS_RESEARCH"
            for item in done["links"]
        )
        # both are kept in one call, for one change
        kept = await rpc(
            "hypothesis/link/recheck",
            project_id="p",
            hypothesis_ids=rain_ids,
            reason_code="STILL_MATCHES",
            current_verdict_revision=seen,
        )
        assert all(
            item["state"] == "RECHECKED" and item["recheck"]["reason_code"] == "STILL_MATCHES"
            for item in kept["links"]
        )
        assert all(event["actor_id"] == "human:local-user" for event in kept["events"])
        assert kept["reason_distribution"] == {
            "total": 4,
            "flipped": 0,
            "by_reason": {"NEEDS_RESEARCH": 2, "STILL_MATCHES": 2},
        }
        # nothing was overwritten: all four events are still there, oldest first
        _, events = HypothesisLinkReader(runtime.ledger).rechecks("p")
        assert [event.reason_code for event in events] == ["NEEDS_RESEARCH"] * 2 + [
            "STILL_MATCHES"
        ] * 2
        # the verdict changes again: the kept link is stale again until someone looks at that change
        await change_result(rpc, "SYN-RES-SYN-C-DET-RAIN", phase=2, value="0.40", numerator="8")
        again = await links(rpc)
        assert all(again[key]["state"] == "STALE" for key in rain_ids)
        assert again[rain_ids[0]]["current_verdict_revision"] != seen
        # extra fields (an actor the caller names) are not accepted
        message = await rpc.error(
            "hypothesis/link/recheck",
            project_id="p",
            hypothesis_ids=rain_ids,
            reason_code="UNRELATED",
            current_verdict_revision=again[rain_ids[0]]["current_verdict_revision"],
            actor_id="model:x",
        )
        assert message == "invalid method parameters"
        _, events = HypothesisLinkReader(runtime.ledger).rechecks("p")
        assert len(events) == 4  # the refused call wrote nothing
        assert dry_id in again
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_flipped_link_is_kept_only_with_words_and_only_a_person_can_recheck(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel(one=True))
    try:
        await investigate(runtime, rpc, DRY)
        await change_result(rpc, "SYN-RES-SYN-C-DET-DRY", value="0.50", numerator="10")
        (item,) = (await links(rpc)).values()
        params = dict(
            project_id="p",
            hypothesis_ids=[item["hypothesis_id"]],
            current_verdict_revision=item["current_verdict_revision"],
        )
        for reason in ("UNRELATED", "STILL_MATCHES", "OTHER"):
            assert "RECHECK_NOTE_REQUIRED" in await rpc.error(
                "hypothesis/link/recheck", reason_code=reason, **params
            )
        # asking for more research on a flip needs no words
        await rpc("hypothesis/link/recheck", reason_code="NEEDS_RESEARCH", **params)
        done = await rpc(
            "hypothesis/link/recheck",
            reason_code="UNRELATED",
            note="the dry run is a different rig",
            **params,
        )
        assert done["links"][0]["state"] == "RECHECKED"
        assert (
            done["events"][0]["flipped"] is True
            and done["events"][0]["note"] == "the dry run is a different rig"
        )
        assert done["reason_distribution"]["flipped"] == 2
        # a model, an agent or a system is not a person
        # the actor is checked before anything is read or written
        checker = HypothesisLinkRechecks(
            cast(RequestRecords, SimpleNamespace(ledger=runtime.ledger))
        )
        for actor in ("model:gpt", "agent:research", "system:verification-trace", ""):
            with pytest.raises(RecheckRefused, match="RECHECK_HUMAN_ONLY"):
                checker.recheck(
                    project_id="p",
                    hypothesis_ids=(item["hypothesis_id"],),
                    reason="UNRELATED",
                    note="x",
                    actor_id=actor,
                    current_verdict_revision=item["current_verdict_revision"],
                )
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_stale_hypothesis_keeps_its_actions_out_of_approval_and_execution_until_rechecked(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel(one=True))
    try:
        await investigate(
            runtime, rpc, None
        )  # an action whose hypothesis has no link: never held back
        linked_thread = await investigate(runtime, rpc, RAIN)
        actions = (await rpc("action/list", project_id="p"))["actions"]
        assert len(actions) == 4
        plans = {}
        for action in actions:
            read = await rpc("action/read", project_id="p", action_id=action["action_id"])
            plans[action["action_id"]] = read["action"]
        (hypothesis_id,) = (await links(rpc)).keys()
        by_link = {
            key: value for key, value in plans.items() if hypothesis_id in value["hypothesis_refs"]
        }
        free = {key: value for key, value in plans.items() if key not in by_link}
        assert by_link and free and linked_thread

        freshness = ResearchFreshnessService(runtime.ledger)

        def eligible(action: dict[str, Any]) -> bool:
            try:
                freshness.require_action_eligible(
                    "p", f"ACTION:{action['action_id']}", action["revision_digest"]
                )
            except ValueError as exc:
                assert str(exc) == "HYPOTHESIS_BASIS_CHANGED"
                return False
            return True

        assert all(eligible(action) for action in plans.values())  # the verdict has not changed
        await rpc.rain_arrives()
        assert not any(eligible(action) for action in by_link.values())  # stale: held back
        assert all(eligible(action) for action in free.values())  # no link: not a false rejection
        item = next(iter((await links(rpc)).values()))
        params = dict(
            project_id="p",
            hypothesis_ids=[hypothesis_id],
            current_verdict_revision=item["current_verdict_revision"],
        )
        await rpc("hypothesis/link/recheck", reason_code="NEEDS_RESEARCH", **params)
        assert not any(
            eligible(action) for action in by_link.values()
        )  # asking for research does not release
        await rpc("hypothesis/link/recheck", reason_code="STILL_MATCHES", **params)
        assert all(eligible(action) for action in plans.values())  # a person looked at this change
        await change_result(rpc, "SYN-RES-SYN-C-DET-RAIN", phase=2, value="0.40", numerator="8")
        assert not any(eligible(action) for action in by_link.values())  # and it changed again
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_preparing_an_approval_is_refused_for_a_plan_on_a_changed_verdict_and_not_otherwise(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel(one=True))
    try:
        await investigate(runtime, rpc, RAIN)
        (hypothesis,) = (await links(rpc)).values()
        action = (await rpc("action/list", project_id="p"))["actions"][0]
        plan = (
            await rpc("action/plan/read", project_id="p", plan_id=f"plan:{action['object_id']}")
        )["plan"]

        async def prepare() -> str:
            params = dict(
                project_id="p",
                plan_id=plan["plan_id"],
                step_id=plan["steps"][0]["step_id"],
                plan_revision_digest=plan["revision_digest"],
                predecessor_output_digests=[],
                target_baseline_digests=[],
                policy_version="project-policy:current",
            )
            response = await rpc.raw("action/authorization/prepare", **params)
            return (
                response.error.message if response.error else str(response.result["value"]["state"])
            )

        assert (
            await prepare() == "NOT_REQUIRED"
        )  # the verdict has not changed: nothing is held back
        await rpc.rain_arrives()
        assert await prepare() == "HYPOTHESIS_BASIS_CHANGED"
        params = dict(
            project_id="p",
            hypothesis_ids=[hypothesis["hypothesis_id"]],
            current_verdict_revision=(await links(rpc))[hypothesis["hypothesis_id"]][
                "current_verdict_revision"
            ],
        )
        await rpc("hypothesis/link/recheck", reason_code="NEEDS_RESEARCH", **params)
        assert await prepare() == "HYPOTHESIS_BASIS_CHANGED"
        await rpc("hypothesis/link/recheck", reason_code="UNRELATED", **params)
        assert await prepare() == "NOT_REQUIRED"  # a person looked at this very change
    finally:
        runtime.close()
