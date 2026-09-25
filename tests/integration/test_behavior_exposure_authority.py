from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from tests.integration.behavior_exposure_helpers import exposure_harness
from tests.integration.storage_coverage_helpers import value

from thoth.adapters.runtime import SystemClock
from thoth.adapters.storage.behavior_execution import SqliteBehaviorExecutionStore
from thoth.domain.behavior_execution import BehaviorApproval, BehaviorExposureRecord
from thoth.domain.behavior_policy import BehaviorPolicyError
from thoth.domain.evaluation_run import sealed_payload


async def test_revoked_approver_withdraws_canary_before_normal_model_input(tmp_path: Path) -> None:
    async with exposure_harness(tmp_path) as h:
        await h.arm()
        project = value(await h.call("project/read", "before-revoke", {}))
        value(
            await h.call(
                "project/role/revoke",
                "revoke-owner",
                {
                    "expected_revision": project["revision"],
                    "role_assignment_id": h.role,
                },
            )
        )
        value(await h.call("thread/input", "after-revoke", {"thread_id": h.thread}))
        assert set(h.model.guidance) == {"baseline-guidance"}
        exposure = value(
            await h.call(
                "improvement/exposure/runtime/read",
                "revoked-read",
                {
                    "exposure_id": h.exposure["spec"]["exposure_id"],
                },
            )
        )["exposure"]
        assert exposure["state"] == "ROLLED_BACK" and exposure["request_count"] == 0


async def test_overlapping_canary_is_rejected_without_claiming_second_execution(
    tmp_path: Path,
) -> None:
    async with exposure_harness(tmp_path) as h:
        await h.arm()
        spec = h.exposure["spec"]
        second = value(
            await h.call(
                "improvement/exposure/prepare",
                "overlap-preview",
                {
                    "improvement_revision_id": h.pair.proposal["record_id"],
                    "evaluation_plan_id": h.pair.plan["record_id"],
                    "requested_exposure_state": "CANARY",
                    "scope": {"environment": "LOCAL", "thread_id": h.thread},
                    "duration_budget": {
                        "duration_seconds": 300,
                        "max_requests": 2,
                        "max_model_calls": 8,
                        "max_billed_cost_microunits": 0,
                    },
                    "stop_rollback_contract": {"rollback_target_digest": spec["baseline_digest"]},
                },
            )
        )["runtime_exposure"]
        identifier = second["spec"]["exposure_id"]
        value(
            await h.call(
                "improvement/exposure/decide",
                "overlap-approve",
                {
                    "exposure_id": identifier,
                    "expected_revision": 1,
                    "approved_digest": second["spec"]["spec_digest"],
                    "decision": "APPROVE",
                    "actor_ref": "human:policy-owner",
                    "role_assignment_ref": h.role,
                },
            )
        )
        rejected = await h.call(
            "improvement/exposure/start",
            "overlap-start",
            {
                "exposure_id": identifier,
                "expected_revision": 2,
            },
        )
        assert rejected.error is not None and rejected.error.data is not None
        assert rejected.error.data["reason_code"] == "BEHAVIOR_CANARY_OVERLAP"
        after = value(
            await h.call(
                "improvement/exposure/runtime/read", "overlap-read", {"exposure_id": identifier}
            )
        )["exposure"]
        assert (
            after["state"] == "APPROVED"
            and after["request_count"] == 0
            and after["observations"] == []
        )


async def test_exposure_owner_has_one_cas_winner_under_concurrent_writers(tmp_path: Path) -> None:
    async with exposure_harness(tmp_path) as h:
        store = SqliteBehaviorExecutionStore(h.pair.runtime.ledger.engine)
        original = store.read(h.pair.project, h.exposure["spec"]["exposure_id"])
        assert original is not None
        payload = original.model_dump(mode="python", exclude={"record_digest"})
        payload.update(
            revision=2,
            state="APPROVED",
            approval=BehaviorApproval(
                actor_ref="human:policy-owner",
                role_assignment_ref=h.role,
                session_ref=None,
                approved_digest=original.spec.spec_digest,
                approved_at=SystemClock().now(),
            ),
        )
        candidate = BehaviorExposureRecord.model_validate(
            sealed_payload("BEHAVIOR_EXPOSURE_RECORD", "record_digest", payload)
        )
        barrier = Barrier(2)

        def contend() -> str:
            barrier.wait(timeout=10)
            try:
                SqliteBehaviorExecutionStore(h.pair.runtime.ledger.engine).append(
                    candidate, expected_revision=1
                )
                return "COMMITTED"
            except BehaviorPolicyError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(contend) for _ in range(2)]
            results = sorted(future.result(timeout=15) for future in futures)
        assert results == ["BEHAVIOR_EXPOSURE_REVISION_CONFLICT", "COMMITTED"]
        current = store.read(h.pair.project, original.spec.exposure_id)
        assert (
            current is not None
            and current.revision == 2
            and current.record_digest == candidate.record_digest
        )
