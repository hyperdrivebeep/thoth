from datetime import datetime
from pathlib import Path
from typing import Literal, TypedDict, cast

from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a02_autonomous_acquisition import prepare_thread
from tests.integration.test_research_focus_basis import research_host
from tests.integration.test_restore_apply_atomicity import CHANGES, handler, input_for
from tests.integration.test_restore_preview_contract import prepared, revise

from thoth.domain.canonical import head_set_digest
from thoth.domain.revision import ImpactPropagationPlan


class _MemoryContextParameters(TypedDict):
    project_id: str
    thread_id: str
    query: str
    target_use: Literal["WORKING_CONTEXT"]
    scope: dict[str, str]
    cutoff_at: datetime


def _record(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in cast(dict[object, object], value))
    return cast(dict[str, object], value)


async def test_restored_basis_blocks_action_preflight_and_new_execution(
    tmp_path: Path,
) -> None:
    runtime, model, accepted, candidates = await prepared(tmp_path)
    try:
        host = handler(runtime)
        revision, snap = candidates["hypothesis.v1"]
        thread_before = value(
            await runtime.bus.query(
                request(
                    "thread/read",
                    "initial-basis",
                    {"project_id": "p", "thread_id": accepted["thread_id"], "contract_version": 2},
                )
            )
        )
        basis = thread_before["current_result"]["research_basis"]
        assert f"HYPOTHESIS:{revision.entity_id}" not in basis["consumed_heads"]
        assert (
            basis["produced_final_heads"][f"HYPOTHESIS:{revision.entity_id}"]
            == revision.revision_digest
        )
        changed = revise(runtime, revision, snap, *CHANGES["hypothesis.v1"])
        payload = await input_for(runtime, revision, changed, "preview")
        calls = len(model.calls)
        value(await runtime.bus.dispatch(request("revision/restore/apply", "restore", payload)))
        plan_id = candidates["action-plan.v1"][0].entity_id
        plan = value(
            await runtime.bus.dispatch(
                request("action/plan/read", "plan", {"project_id": "p", "plan_id": plan_id})
            )
        )
        assert plan["currentness"]["state"] == "REVIEW_REQUIRED"
        assert plan["currentness"]["execution_eligible"] is False
        executions_before = host.planner.executions.list_executions("p")
        denied = await runtime.bus.dispatch(
            request(
                "execution/start",
                "forbidden-dispatch",
                {
                    "project_id": "p",
                    "plan_id": plan_id,
                    "execution_profile_ref": "fixture",
                    "plan_revision_digest": plan["plan"]["revision_digest"],
                    "expected_working_head_digest": head_set_digest(runtime.ledger.read_heads("p")),
                },
            )
        )
        assert denied.error is not None and "DEPENDENCY_REVIEW_REQUIRED" in denied.error.message
        assert host.planner.executions.list_executions("p") == executions_before
        assert len(model.calls) == calls
        # A new bounded HOLD is a valid partial result; it cannot clear this review debt.
        model.missing = True
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "new-held-review",
                    {
                        "project_id": "p",
                        "thread_id": accepted["thread_id"],
                        "instruction": "Reconsider the same evidence",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        current = value(
            await runtime.bus.query(
                request(
                    "thread/read",
                    "still-held",
                    {"project_id": "p", "thread_id": accepted["thread_id"], "contract_version": 2},
                )
            )
        )
        assert current["basis_currentness"]["state"] != "CURRENT"
        from thoth.domain.enums import HypothesisStatus, ModelRole

        generator_calls = [
            call for call in model.calls[calls:] if call.role == ModelRole.HYPOTHESIS_GENERATOR
        ]
        assert generator_calls
        context = generator_calls[-1].context_pack
        assert (
            _record(_record(context.research_context["entity_currentness"])[f"HYPOTHESIS:{revision.entity_id}"])["state"]
            == "REVIEW_REQUIRED"
        )
        candidate = next(
            record
            for record in context.canonical_hypotheses
            if record.hypothesis_id == revision.entity_id
        )
        assert candidate.empirical_appraisal == "UNASSESSED"
        assert candidate.generation_details is not None
        assert candidate.generation_details.candidate_status == HypothesisStatus.DRAFT
    finally:
        runtime.close()


async def test_memory_with_unchanged_owner_head_is_excluded_by_dependency_state(
    tmp_path: Path,
) -> None:
    runtime, _connector, project = await prepare_thread(tmp_path, allow_connector=True)
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "memory-seed",
                    {"project_id": project, "thread_id": f"thread:{project}"},
                )
            )
        )
        research = research_host(runtime)
        service = research.analysis.memory
        thread = research.threads.read(f"thread:{project}")
        assert thread is not None
        project_record = research.projects.read(project)
        assert project_record is not None
        parameters: _MemoryContextParameters = {
            "project_id": project,
            "thread_id": thread.thread_id,
            "query": "",
            "target_use": "WORKING_CONTEXT",
            "scope": thread.scope,
            "cutoff_at": project_record.cutoff_at,
        }
        before = service.build_context(**parameters)
        assert before.included
        owner = before.included[0].owner_revision_ref
        revision = runtime.ledger.read_revision_by_digest(project, owner)
        assert revision is not None
        key = f"{revision.entity_type.value}:{revision.entity_id}"
        with runtime.ledger.transaction() as transaction:
            transaction.apply_impact_plan(
                project,
                ImpactPropagationPlan(recalculate_refs=(key,)),
                caused_by_revision=owner,
                updated_at=revision.created_at.isoformat(),
            )
        after = service.build_context(**parameters)
        assert runtime.ledger.read_heads(project)[key] == owner
        assert all(item.owner_revision_ref != owner for item in after.included)
        assert after.excluded_reason_counts["DEPENDENCY_REVIEW_REQUIRED"] >= 1
    finally:
        runtime.close()
