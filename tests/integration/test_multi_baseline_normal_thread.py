from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from tests.integration.test_a02_autonomous_acquisition import prepare_thread, request, value

from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage import SqliteBaselineStore
from thoth.application.services import BaselineRouter, BaselineService
from thoth.domain.auth import AuthenticatedActorContext, authenticated_actor_scope


@pytest.mark.asyncio
async def test_normal_thread_proposes_scoped_baselines_without_moving_milestone(
    tmp_path: Path,
) -> None:
    runtime, _connector, project_id = await prepare_thread(tmp_path, allow_connector=True)
    try:
        analyzed = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "p2-thread-input",
                    {"project_id": project_id, "thread_id": f"thread:{project_id}"},
                )
            )
        )
        multi = cast(dict[str, JsonValue], analyzed["multi_baseline"])
        head_set = cast(dict[str, JsonValue], multi["project_head_set"])
        candidates = cast(list[dict[str, JsonValue]], multi["baseline_candidates"])
        commit = cast(dict[str, JsonValue], analyzed["commit"])
        assert head_set["head_map"]
        assert head_set["head_set_digest"] == commit["after_head_set_digest"]
        assert {str(item["scope"]) for item in candidates} >= {
            "RESEARCH_HYPOTHESIS",
            "EVALUATION_EVIDENCE",
        }
        assert all(item["state"] == "PENDING_PROTECTED_DECISION" for item in candidates)
        assert all(item["protected"] is True for item in candidates)
        assert multi["current_baseline_sets"] == []

        listed = value(
            await runtime.bus.dispatch(
                request(
                    "revision/baseline/list",
                    "p2-list-before-decision",
                    {"project_id": project_id},
                )
            )
        )
        assert listed["typed_baseline_sets"] == []
        assert cast(list[object], listed["typed_baseline_candidates"])
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_baseline_decision_uow_fault_rolls_back_set_and_decision(tmp_path: Path) -> None:
    runtime, _connector, project_id = await prepare_thread(tmp_path, allow_connector=True)
    try:
        analyzed = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "p2-fault-seed",
                    {"project_id": project_id, "thread_id": f"thread:{project_id}"},
                )
            )
        )
        candidate = cast(
            list[dict[str, JsonValue]],
            cast(dict[str, JsonValue], analyzed["multi_baseline"])["baseline_candidates"],
        )[0]

        def fail(step: str) -> None:
            if step == "after_baseline":
                raise RuntimeError("injected baseline UoW failure")

        store = SqliteBaselineStore(runtime.ledger.engine, fault_injector=fail)
        service = BaselineService(
            store=store,
            ledger=runtime.ledger,
            router=BaselineRouter(),
            clock=SystemClock(),
            ids=UuidIdGenerator(),
        )
        with pytest.raises(RuntimeError, match="injected baseline UoW failure"):
            service.decide(
                project_id=project_id,
                candidate_id=str(candidate["candidate_id"]),
                decision="APPROVE",
                actor_ref="human:p2-owner",
                role_assignment_ref="role:p2-owner",
                approved_digest=str(candidate["candidate_digest"]),
            )
        assert store.list_sets(project_id) == ()
        persisted = store.read_candidate(project_id, str(candidate["candidate_id"]))
        assert persisted is not None
        assert persisted.state == "PENDING_PROTECTED_DECISION"
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_protected_decision_freezes_one_scope_and_routine_head_does_not_move_it(
    tmp_path: Path,
) -> None:
    runtime, _connector, project_id = await prepare_thread(tmp_path, allow_connector=True)
    try:
        first = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "p2-first-input",
                    {"project_id": project_id, "thread_id": f"thread:{project_id}"},
                )
            )
        )
        candidates = cast(
            list[dict[str, JsonValue]],
            cast(dict[str, JsonValue], first["multi_baseline"])["baseline_candidates"],
        )
        candidate = next(item for item in candidates if item["scope"] == "RESEARCH_HYPOTHESIS")
        project = value(
            await runtime.bus.dispatch(
                request("project/read", "p2-project-read", {"project_id": project_id})
            )
        )
        assigned = value(
            await runtime.bus.dispatch(
                request(
                    "project/role/assign",
                    "p2-baseline-role",
                    {
                        "project_id": project_id,
                        "expected_revision": project["revision"],
                        "actor_id": "human:p2-owner",
                        "role": "baseline-owner",
                        "scope": "PROJECT",
                        "authority_tags": ["BASELINE_OWNER"],
                    },
                )
            )
        )
        role = cast(dict[str, JsonValue], assigned["role"])

        spoof_candidate = next(item for item in candidates if item is not candidate)
        attacker = AuthenticatedActorContext(
            actor_id="human:p2-attacker",
            session_id="session:p2-attacker",
            project_id=project_id,
            role_assignment_id="role:p2-attacker",
            role="revision-writer",
            capabilities=("WRITE", "REVISION"),
            data_scopes=("PROJECT",),
        )
        with authenticated_actor_scope(attacker):
            spoofed = await runtime.bus.dispatch(
                request(
                    "revision/baseline/decide",
                    "p2-baseline-spoofed",
                    {
                        "project_id": project_id,
                        "baseline_candidate_id": spoof_candidate["candidate_id"],
                        "decision": "APPROVE",
                        "actor_ref": "human:p2-owner",
                        "role_assignment_ref": role["role_assignment_id"],
                        "approved_digest": spoof_candidate["candidate_digest"],
                    },
                )
            )
        assert spoofed.error is not None
        assert spoofed.error.code == -32040

        denied = await runtime.bus.dispatch(
            request(
                "revision/baseline/decide",
                "p2-baseline-denied",
                {
                    "project_id": project_id,
                    "baseline_candidate_id": candidate["candidate_id"],
                    "decision": "APPROVE",
                    "actor_ref": "human:not-owner",
                    "role_assignment_ref": role["role_assignment_id"],
                    "approved_digest": candidate["candidate_digest"],
                },
            )
        )
        assert denied.error is not None

        decided = value(
            await runtime.bus.dispatch(
                request(
                    "revision/baseline/decide",
                    "p2-baseline-decide",
                    {
                        "project_id": project_id,
                        "baseline_candidate_id": candidate["candidate_id"],
                        "decision": "APPROVE",
                        "actor_ref": "human:p2-owner",
                        "role_assignment_ref": role["role_assignment_id"],
                        "approved_digest": candidate["candidate_digest"],
                    },
                )
            )
        )
        baseline = cast(dict[str, JsonValue], decided["typed_baseline_set"])
        original_manifest = cast(dict[str, str], baseline["head_map"])
        assert baseline["lifecycle"] == "CURRENT"
        assert decided["protected_decision"] is True

        plan = cast(dict[str, JsonValue], first["action_plan"])
        action_head = runtime.ledger.read_heads(project_id)[f"ACTION:{plan['plan_id']}"]
        object_id = str(plan["object_id"])
        invalid_comparator = await runtime.bus.dispatch(
            request(
                "outcome/series/create",
                "p2-invalid-comparator",
                {
                    "project_id": project_id,
                    "object_id": object_id,
                    "action_plan_revision_digest": action_head,
                    "profile_ref": "EXPERIMENT_LEARNING_OUTCOME",
                    "comparison_baseline_set_digest": "f" * 64,
                    "assessment_windows": [{"assessment_phase": "INTERIM"}],
                },
            )
        )
        assert invalid_comparator.error is not None
        assert (
            invalid_comparator.error.message
            == "Outcome comparator requires a current typed BaselineSet"
        )
        valid_comparator = value(
            await runtime.bus.dispatch(
                request(
                    "outcome/series/create",
                    "p2-valid-comparator",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "action_plan_revision_digest": action_head,
                        "profile_ref": "EXPERIMENT_LEARNING_OUTCOME",
                        "comparison_baseline_set_digest": baseline["baseline_set_digest"],
                        "assessment_windows": [{"assessment_phase": "INTERIM"}],
                    },
                )
            )
        )
        assert (
            cast(dict[str, JsonValue], valid_comparator["series"])["comparison_baseline_set_digest"]
            == baseline["baseline_set_digest"]
        )

        value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "p2-second-input",
                    {
                        "project_id": project_id,
                        "thread_id": f"thread:{project_id}",
                        "instruction": "Reassess without moving the protected milestone baseline.",
                    },
                )
            )
        )
        listed = value(
            await runtime.bus.dispatch(
                request(
                    "revision/baseline/list",
                    "p2-list-after-routine",
                    {"project_id": project_id},
                )
            )
        )
        current = next(
            item
            for item in cast(list[dict[str, JsonValue]], listed["typed_baseline_sets"])
            if item["baseline_set_id"] == baseline["baseline_set_id"]
        )
        assert cast(dict[str, str], current["head_map"]) == original_manifest
        assert current["lifecycle"] == "STALE"
        assert current["recalculation_required"] is True
    finally:
        runtime.close()
