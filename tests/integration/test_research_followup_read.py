from pathlib import Path
from types import MethodType
from typing import cast

from pytest import MonkeyPatch
from tests.atomicity.harness import assert_phase_delta, snapshot
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.adapters.storage.threads import SqliteThreadStore
from thoth.application.commands.research_followup import ResearchFollowupHandlers
from thoth.apps.runtime_types import AppRuntime
from thoth.domain.auth import AuthenticatedActorContext, authenticated_actor_scope
from thoth.domain.research_followup import ProjectReviewListInput
from thoth.protocol.registry import MethodRegistry


def _followup_handlers(runtime: AppRuntime) -> ResearchFollowupHandlers:
    registry = cast(MethodRegistry, vars(runtime.bus)["_registry"])
    return cast(
        ResearchFollowupHandlers,
        cast(MethodType, registry.resolve("project/review/list")).__self__,
    )


def _result_digest_for(
    runtime: AppRuntime, project_id: str, thread_id: str, request_digest: str
) -> str:
    for revision in runtime.ledger.read_revisions(
        project_id, "DECISION_OBJECT", f"result:{thread_id}"
    ):
        snapshot_record = runtime.ledger.read_snapshot(revision.snapshot_id)
        if snapshot_record is None:
            continue
        ref = snapshot_record.content.get("request_ref")
        if isinstance(ref, dict):
            ref_map = cast(dict[str, object], ref)
            if ref_map.get("revision_digest") == request_digest:
                return revision.revision_digest
    raise AssertionError(f"result revision not found for {request_digest}")


async def test_thread_read_projects_user_progress_and_coverage(tmp_path: Path) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=True)
    try:
        accepted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start",
                    {
                        "project_id": "p",
                        "problem": "LAB-42 지연 조건을 요약해줘",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        before = snapshot(runtime.ledger.engine)
        readback = value(
            await runtime.bus.query(
                request(
                    "thread/read",
                    "read",
                    {"project_id": "p", "thread_id": accepted["thread_id"]},
                )
            )
        )
        assert readback["user_progress_summary"]["schema_version"] == "1.0.0"
        assert readback["user_progress_summary"]["state"] == "COMPLETE"
        assert readback["coverage_matrix"]["summary"]["satisfied"] >= 1
        assert readback["coverage_matrix"]["summary"]["unresolved"] == 0
        assert readback["next_user_action"]["action_type"] == "NONE"
        assert readback["current_result"]["result"]["answer"]
        assert_phase_delta(before, snapshot(runtime.ledger.engine))
    finally:
        runtime.close()


async def test_compare_exact_results_and_project_review_list_are_read_only(
    tmp_path: Path,
) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    try:
        first = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "first",
                    {
                        "project_id": "p",
                        "problem": "첫 질문",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "second",
                    {
                        "project_id": "p",
                        "thread_id": first["thread_id"],
                        "instruction": "두 번째 조건",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        conversation = value(
            await runtime.bus.query(
                request(
                    "thread/activity/list",
                    "conversation",
                    {"project_id": "p", "thread_id": first["thread_id"]},
                )
            )
        )["conversation"]
        before_request = conversation["turns"][0]["request_revision_digest"]
        after_request = conversation["turns"][1]["request_revision_digest"]
        before_result = _result_digest_for(runtime, "p", first["thread_id"], before_request)
        after_result = _result_digest_for(runtime, "p", first["thread_id"], after_request)
        phase = snapshot(runtime.ledger.engine)
        delta = value(
            await runtime.bus.query(
                request(
                    "thread/result/compare/read",
                    "compare",
                    {
                        "project_id": "p",
                        "thread_id": first["thread_id"],
                        "before": {
                            "request_revision_digest": before_request,
                            "result_revision_digest": before_result,
                        },
                        "after": {
                            "request_revision_digest": after_request,
                            "result_revision_digest": after_result,
                        },
                    },
                )
            )
        )
        assert delta["state"] in {"CHANGED", "NO_CHANGE"}
        assert delta["reason_state"] in {"RECORDED", "UNKNOWN_REASON"}
        review = value(
            await runtime.bus.query(
                request(
                    "project/review/list",
                    "review",
                    {"project_id": "p", "limit": 10},
                )
            )
        )
        assert review["unread_supported"] is False
        assert review["assignment_supported"] is False
        assert review["items"]
        assert review["items"][0]["next_user_action"]["action_type"] in {
            "REVIEW_GAPS",
            "REVIEW_CURRENTNESS",
            "OPEN_RESULT_DETAIL",
        }
        assert_phase_delta(phase, snapshot(runtime.ledger.engine))
    finally:
        runtime.close()


async def test_review_list_hides_results_without_result_access(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    try:
        accepted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start",
                    {"project_id": "p", "problem": "Scoped review", "contract_version": 2},
                )
            )
        )
        await runtime.bus.drain()
        reviews = _followup_handlers(runtime).reviews
        thread = reviews.threads.read(accepted["thread_id"])
        assert thread is not None
        result = runtime.ledger.read_heads("p")[f"DECISION_OBJECT:result:{thread.thread_id}"]
        original = reviews.access.may_read_revision

        def restricted_read(project_id: str, digest: str) -> bool:
            return digest != result and original(project_id, digest)

        monkeypatch.setattr(
            reviews.access,
            "may_read_revision",
            restricted_read,
        )
        assert reviews.list(ProjectReviewListInput(project_id="p")).items == ()
    finally:
        runtime.close()


async def test_review_list_hides_other_workstream_before_reading_thread(
    tmp_path: Path,
) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    try:
        accepted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start",
                    {"project_id": "p", "problem": "Scoped review", "contract_version": 2},
                )
            )
        )
        await runtime.bus.drain()
        reviews = _followup_handlers(runtime).reviews
        threads = SqliteThreadStore(runtime.ledger.engine)
        thread = threads.read(accepted["thread_id"])
        assert thread is not None
        assert threads.update(
            thread.model_copy(update={"scope": {"workstream": "beta"}}),
            expected_revision=thread.revision,
        )
        actor = AuthenticatedActorContext(
            actor_id="human:alpha",
            session_id="alpha-session",
            project_id="p",
            role_assignment_id="alpha-role",
            role="researcher",
            capabilities=("READ",),
            data_scopes=("WORKSTREAM:alpha",),
        )
        with authenticated_actor_scope(actor):
            assert reviews.list(ProjectReviewListInput(project_id="p")).items == ()
    finally:
        runtime.close()
