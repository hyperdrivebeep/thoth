"""Actions and approvals on a hypothesis whose verdict changed, and an action draft from a test.

A stale hypothesis is kept out of a new action and out of an approval decision (not only out of
preparing one). A changed basis under the same verdict is told apart. An action draft made from a
discriminating test takes its risk from the effect declaration and the rules, never from the
tier the model wrote on the test, and calls no model. These tests go through the public methods.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from tests.integration.test_hypothesis_link import (
    change_result,
    investigate,
    links,
    verdict_state,
)
from tests.integration.test_research_request_v2 import ControlledResearchModel
from tests.integration.test_trace_origin import DRY, RAIN, Rpc, opened

from thoth.application.services.authorization_consumption import decide_authorization
from thoth.apps.runtime import AppRuntime
from thoth.domain.action_full import AuthorizationEnvelopeRecord

CHANGED = "HYPOTHESIS_BASIS_CHANGED"


async def hypothesis_ids(rpc: Rpc) -> list[str]:
    found = (await rpc("hypothesis/list", project_id="p"))["hypotheses"]
    return [item["hypothesis_id"] for item in found]


async def create_on(rpc: Rpc, hypothesis_id: str, method: str = "action/create") -> dict[str, Any]:
    """The call a person's screen makes to add an action on this hypothesis' object."""
    read = (await rpc("hypothesis/read", project_id="p", hypothesis_id=hypothesis_id))["hypothesis"]
    base: dict[str, Any] = dict(
        project_id="p", object_id=read["object_id"], hypothesis_refs=[hypothesis_id]
    )
    if method == "action/generate":
        return dict(**base, decision_need="Look again", evidence_scope=read["evidence_refs"])
    return dict(
        **base,
        primary_purpose="INFORMATION_ACQUISITION",
        evidence_refs=read["evidence_refs"],
        specification={
            "description": "Look again",
            "expected_observation_or_change": {"description": "a reading", "status": "CANDIDATE"},
            "effect_completeness_confirmed": True,
            "effect_vector": {"effect_completeness_confirmed": True},
            "stop_conditions": ["a reading is taken"],
            "observability": "receipt",
        },
    )


async def action_count(rpc: Rpc) -> int:
    return len((await rpc("action/list", project_id="p"))["actions"])


async def recheck(rpc: Rpc, hypothesis_id: str, reason: str) -> None:
    item = (await links(rpc))[hypothesis_id]
    await rpc(
        "hypothesis/link/recheck",
        project_id="p",
        hypothesis_ids=[hypothesis_id],
        reason_code=reason,
        current_verdict_revision=item["current_verdict_revision"],
    )


@pytest.mark.asyncio
async def test_a_new_action_on_a_stale_hypothesis_is_refused_and_not_otherwise(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel(one=True))
    try:
        await investigate(runtime, rpc, None)  # its hypothesis has no link: never held back
        await investigate(runtime, rpc, RAIN)
        (linked,) = (await links(rpc)).keys()
        (plain,) = [item for item in await hypothesis_ids(rpc) if item != linked]
        before = await action_count(rpc)
        await rpc("action/create", **await create_on(rpc, linked))  # the verdict has not changed
        await rpc.rain_arrives()
        for method in ("action/create", "action/generate"):
            refused = await rpc.error(method, **await create_on(rpc, linked, method))
            assert CHANGED in refused
        assert await action_count(rpc) == before + 1  # the refused calls wrote nothing
        await rpc("action/create", **await create_on(rpc, plain))  # no link: not a false rejection
        await rpc("action/generate", **await create_on(rpc, plain, "action/generate"))
        await recheck(rpc, linked, "NEEDS_RESEARCH")
        assert CHANGED in await rpc.error("action/create", **await create_on(rpc, linked))
        await recheck(rpc, linked, "STILL_MATCHES")  # a person looked at this very change
        await rpc("action/create", **await create_on(rpc, linked))
    finally:
        runtime.close()


def envelope_for(plan: dict[str, Any]) -> AuthorizationEnvelopeRecord:
    now = datetime.now(UTC)
    return AuthorizationEnvelopeRecord(
        authorization_revision_id="authorization-revision:1",
        authorization_id="authorization:1",
        project_id="p",
        plan_id=plan["plan_id"],
        step_id=plan["steps"][0]["step_id"],
        plan_revision_digest=plan["revision_digest"],
        predecessor_output_digests=(),
        target_baseline_digests=(),
        policy_version="policy:current",
        exact_scope_digest="a" * 64,
        required_roles=("project-owner",),
        expires_at=now + timedelta(days=1),
        revision_digest="b" * 64,
        created_at=now,
    )


@pytest.mark.asyncio
async def test_an_approval_that_was_prepared_in_time_is_refused_when_decided_on_a_changed_basis(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel(one=True))
    try:
        await investigate(runtime, rpc, RAIN)
        (linked,) = (await links(rpc)).keys()
        action = (await rpc("action/list", project_id="p"))["actions"][0]
        plan = (
            await rpc("action/plan/read", project_id="p", plan_id=f"plan:{action['object_id']}")
        )["plan"]
        pending = envelope_for(plan)  # prepared while the verdict still stood

        def decide(decision: str) -> None:
            """Past the stale check the call reaches the role check, which has no roles here."""
            decide_authorization(
                pending,
                decision=decision,
                actor_ref="human:owner",
                role_assignment_ref="role:owner",
                approved_digest=pending.exact_scope_digest,
                reason=None,
                dissent=None,
                store=cast(Any, SimpleNamespace(read_authorization=lambda *_: pending)),
                governance=cast(Any, SimpleNamespace(list_roles=lambda _: [])),
                ledger=runtime.ledger,
                clock=cast(Any, SimpleNamespace(now=lambda: datetime.now(UTC))),
                ids=cast(Any, None),
                audit=lambda *_: cast(Any, None),
            )

        no_role = "non-fungible role"
        with pytest.raises(ValueError, match=no_role):
            decide("APPROVE")  # nothing has changed: not a false rejection
        await rpc.rain_arrives()
        with pytest.raises(ValueError, match=CHANGED):
            decide("APPROVE")
        with pytest.raises(ValueError, match=no_role):
            decide("REJECT")  # saying no to a stale basis is always allowed
        await recheck(rpc, linked, "NEEDS_RESEARCH")
        with pytest.raises(ValueError, match=CHANGED):
            decide("APPROVE")
        await recheck(rpc, linked, "UNRELATED")
        with pytest.raises(ValueError, match=no_role):
            decide("APPROVE")
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_results_that_change_under_the_same_verdict_are_told_apart_but_still_stale(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel(one=True))
    try:
        await investigate(runtime, rpc, DRY)
        assert await verdict_state(rpc, DRY) == "PASS_COMPUTED"
        # 18 of 20 is 0.90: a new result revision, and the row is still met
        await change_result(rpc, "SYN-RES-SYN-C-DET-DRY", value="0.90", numerator="18")
        assert await verdict_state(rpc, DRY) == "PASS_COMPUTED"
        (item,) = (await links(rpc)).values()
        assert (item["state"], item["change"]) == ("STALE", "EVIDENCE_ONLY")
        assert (item["link_state"], item["current_state"]) == ("PASS_COMPUTED", "PASS_COMPUTED")
        # the rule for approval and for execution is the same as for any other change
        assert CHANGED in await rpc.error(
            "action/create", **await create_on(rpc, item["hypothesis_id"])
        )
        # a person's recheck covers it like any other change (no flip, so no words needed)
        await recheck(rpc, item["hypothesis_id"], "STILL_MATCHES")
        assert next(iter((await links(rpc)).values()))["state"] == "RECHECKED"
        # a value that makes the row fail is a different kind of change
        await change_result(rpc, "SYN-RES-SYN-C-DET-DRY", phase=1, value="0.50", numerator="10")
        (flipped,) = (await links(rpc)).values()
        assert (flipped["state"], flipped["change"]) == ("STALE", "FLIPPED")
    finally:
        runtime.close()


async def drafted(runtime: AppRuntime, rpc: Rpc, **declaration: Any) -> tuple[dict[str, Any], str]:
    """Press "make an action request from this test" for the one test the model proposed."""
    (hypothesis_id,) = await hypothesis_ids(rpc)
    params: dict[str, Any] = dict(project_id="p", hypothesis_id=hypothesis_id, test_id="test-1")
    if declaration:
        params["effect_declaration"] = declaration
    done = await rpc("action/draft/fromTest", **params)
    return done["action"], hypothesis_id


@pytest.mark.asyncio
async def test_a_draft_from_a_test_gets_its_risk_from_the_effect_declaration_not_from_the_model(
    tmp_path: Path,
) -> None:
    model = ControlledResearchModel(one=True, tested=True)
    runtime, rpc = await opened(tmp_path, model)
    try:
        await investigate(runtime, rpc, None)
        calls = len(model.calls)
        # the model called this test R0; nothing declares what it does, so the rules say R3
        action, hypothesis_id = await drafted(runtime, rpc)
        assert (action["risk_tier"], action["policy_state"]) == ("R3", "POLICY_UNDEFINED")
        assert (
            action["proposal_state"] == "DRAFT" and action["authorization_state"] == "NOT_PREPARED"
        )
        assert action["test_refs"] == [{"hypothesis_id": hypothesis_id, "test_id": "test-1"}]
        assert action["hypothesis_refs"] == [hypothesis_id]
        assert action["specification"]["description"] == "Compare the rain run with a dry run"
        assert len(model.calls) == calls  # a person pressed a button; no model was asked
        read = (await rpc("action/read", project_id="p", action_id=action["action_id"]))["action"]
        assert read["test_refs"] == action["test_refs"]
        # a declaration that says nothing is written to the world and is complete: read only
        read_only, _ = await drafted(
            runtime, rpc, effect_completeness_confirmed=True, external_write=False
        )
        assert read_only["risk_tier"] == "R0"
        # a declaration that writes outside is a protected action, whatever the model said
        written, _ = await drafted(
            runtime, rpc, effect_completeness_confirmed=True, external_write=True
        )
        assert (written["risk_tier"], written["policy_state"]) == ("R3", "APPROVAL_REQUIRED")
        # a forbidden effect stays forbidden
        banned, _ = await drafted(
            runtime, rpc, effect_completeness_confirmed=True, grants_waiver=True
        )
        assert (banned["risk_tier"], banned["policy_state"]) == ("R4", "PROHIBITED")
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_draft_names_the_test_it_came_from_and_refuses_what_it_cannot_find(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel(one=True, tested=True))
    try:
        await investigate(runtime, rpc, None)
        (hypothesis_id,) = await hypothesis_ids(rpc)
        before = await action_count(rpc)
        assert "TEST_NOT_FOUND" in await rpc.error(
            "action/draft/fromTest", project_id="p", hypothesis_id=hypothesis_id, test_id="nope"
        )
        assert "HYPOTHESIS_NOT_FOUND" in await rpc.error(
            "action/draft/fromTest",
            project_id="p",
            hypothesis_id="hypothesis:none",
            test_id="test-1",
        )
        assert await action_count(rpc) == before
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_draft_from_a_test_of_a_stale_hypothesis_is_refused(tmp_path: Path) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel(one=True, tested=True))
    try:
        await investigate(runtime, rpc, RAIN)
        (hypothesis_id,) = await hypothesis_ids(rpc)
        params = dict(project_id="p", hypothesis_id=hypothesis_id, test_id="test-1")
        await rpc("action/draft/fromTest", **params)
        await rpc.rain_arrives()
        assert CHANGED in await rpc.error("action/draft/fromTest", **params)
        await recheck(rpc, hypothesis_id, "STILL_MATCHES")
        await rpc("action/draft/fromTest", **params)
    finally:
        runtime.close()


SCREEN_KEYS = (
    "changes_official_kpi",
    "grants_waiver",
    "changes_safety_threshold",
    "finalizes_model_weights",
    "external_write",
    "physical_action",
    "changes_official_baseline",
    "operational_equipment_change",
    "runs_untrusted_code",
    "sandbox_required",
    "changes_local_draft",
)


@pytest.mark.asyncio
async def test_a_declaration_as_the_screen_sends_it_is_stored_as_written_and_ruled_by_the_server(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel(one=True, tested=True))
    try:
        await investigate(runtime, rpc, None)

        def complete(**chosen: bool) -> dict[str, bool]:
            named = {key: chosen.get(key, False) for key in SCREEN_KEYS}
            return {**named, "effect_completeness_confirmed": True}

        # nothing ticked and the list not called complete: the rule's own protected answer
        empty, _ = await drafted(runtime, rpc, effect_completeness_confirmed=False)
        assert (empty["risk_tier"], empty["policy_state"]) == ("R3", "POLICY_UNDEFINED")
        assert empty["required_roles"] == ["effect-owner"]
        # every effect named false and the list complete: it only reads
        none = complete()
        read_only, _ = await drafted(runtime, rpc, **none)
        assert (read_only["risk_tier"], read_only["policy_state"]) == ("R0", "AUTO_ALLOWED")
        assert read_only["effect_vector"] == none
        # the same list with a local draft, a sandbox or a physical action
        local, _ = await drafted(runtime, rpc, **complete(changes_local_draft=True))
        assert (local["risk_tier"], local["policy_state"]) == ("R1", "PREAUTHORIZED")
        boxed, _ = await drafted(runtime, rpc, **complete(sandbox_required=True))
        assert (boxed["risk_tier"], boxed["required_roles"]) == ("R2", ["sandbox-owner"])
        physical, _ = await drafted(runtime, rpc, **complete(physical_action=True))
        assert (physical["risk_tier"], physical["policy_state"]) == ("R3", "APPROVAL_REQUIRED")
        assert physical["required_roles"] == ["project-owner", "safety-owner"]
        # a forbidden effect alone, with the list not called complete, is still forbidden
        banned, _ = await drafted(
            runtime, rpc, changes_official_kpi=True, effect_completeness_confirmed=False
        )
        assert (banned["risk_tier"], banned["policy_state"]) == ("R4", "PROHIBITED")
        assert banned["effect_vector"]["changes_official_kpi"] is True
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_test_refs_are_an_input_field_covered_by_the_digest_and_outside_the_specification(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel(one=True, tested=True))
    try:
        await investigate(runtime, rpc, None)
        (hypothesis_id,) = await hypothesis_ids(rpc)
        base = await create_on(rpc, hypothesis_id)
        ref = {"hypothesis_id": hypothesis_id, "test_id": "test-1"}
        other = {"hypothesis_id": hypothesis_id, "test_id": "test-2"}
        plain = (await rpc("action/create", **base))["action"]
        one = (await rpc("action/create", **base, test_refs=[ref]))["action"]
        two = (await rpc("action/create", **base, test_refs=[other]))["action"]
        assert plain["test_refs"] == [] and one["test_refs"] == [ref]
        assert (
            "test_refs" not in one["specification"]
        )  # the new way keeps it out of the specification
        # the same specification with only test_refs different is a different revision
        assert len({plain["revision_digest"], one["revision_digest"], two["revision_digest"]}) == 3
        read = (await rpc("action/read", project_id="p", action_id=one["action_id"]))["action"]
        assert read["test_refs"] == [ref] and read["revision_digest"] == one["revision_digest"]
        # a revision keeps the field and its digest still follows it
        revised = (
            await rpc(
                "action/revise",
                project_id="p",
                action_id=one["action_id"],
                expected_revision_digest=one["revision_digest"],
                patch={"specification": {**base["specification"], "description": "Look once more"}},
                evidence_refs=base["evidence_refs"],
                reason="look again",
            )
        )["action"]
        assert (
            revised["test_refs"] == [ref] and revised["revision_digest"] != one["revision_digest"]
        )
        # a test_refs that is not a pair of ids is refused
        bad = await rpc.error("action/create", **base, test_refs=[{"test_id": "x"}])
        assert bad == "invalid method parameters"
        # a record made the old way, with the refs inside its specification, still reads as before
        old = await rpc(
            "action/create",
            **{**base, "specification": {**base["specification"], "test_refs": [ref]}},
        )
        assert old["action"]["test_refs"] == [ref]
    finally:
        runtime.close()
