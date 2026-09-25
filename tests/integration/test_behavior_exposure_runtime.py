from pathlib import Path

from sqlalchemy import select
from tests.integration.behavior_exposure_helpers import exposure_harness
from tests.integration.storage_coverage_helpers import value
from tests.integration.test_a02_autonomous_acquisition import StaticModelResolver

from thoth.adapters.connectors import ConnectorRegistry
from thoth.adapters.evaluators.catalog import FrozenEvaluationCatalog
from thoth.adapters.storage.schema import baseline_sets
from thoth.apps.runtime import create_runtime


async def test_approved_canary_changes_normal_inputs_and_regression_withdraws_it(
    tmp_path: Path,
) -> None:
    async with exposure_harness(tmp_path) as h:
        await h.arm()
        result = value(await h.call("thread/input", "candidate-work", {"thread_id": h.thread}))
        assert h.model.guidance and set(h.model.guidance) == {"candidate-guidance"}
        assert any(item["origin"] == "CANARY" for item in result["behavior_execution"]["snapshots"])
        identifier = h.exposure["spec"]["exposure_id"]
        current = value(
            await h.call(
                "improvement/exposure/runtime/read", "read-used", {"exposure_id": identifier}
            )
        )["exposure"]
        assert current["observations"][0]["decision_exposed"] is True
        assert current["observations"][0]["successful"] is True
        h.model.fail = True
        failed = await h.call("thread/input", "regression", {"thread_id": h.thread})
        assert failed.error is not None
        current = value(
            await h.call(
                "improvement/exposure/runtime/read", "read-rollback", {"exposure_id": identifier}
            )
        )["exposure"]
        assert current["state"] == "ROLLED_BACK"
        h.model.fail = False
        h.model.guidance.clear()
        restored = value(await h.call("thread/input", "restored", {"thread_id": h.thread}))
        assert h.model.guidance and set(h.model.guidance) == {"baseline-guidance"}
        assert all(
            item["origin"] != "CANARY" for item in restored["behavior_execution"]["snapshots"]
        )


async def test_unmetered_canary_is_held_before_model_dispatch(tmp_path: Path) -> None:
    async with exposure_harness(tmp_path, metered=False) as h:
        await h.arm()
        response = await h.call("thread/input", "unknown-cost", {"thread_id": h.thread})
        assert response.error is not None and response.error.data is not None
        assert response.error.data["reason_code"] == "BEHAVIOR_MODEL_COST_UNKNOWN"
        assert h.model.guidance == []
        current = value(
            await h.call(
                "improvement/exposure/runtime/read",
                "held",
                {
                    "exposure_id": h.exposure["spec"]["exposure_id"],
                },
            )
        )["exposure"]
        assert current["state"] == "ROLLED_BACK"
        assert current["observations"][0]["decision_exposed"] is False


async def test_shadow_policy_is_observed_without_changing_normal_inputs(tmp_path: Path) -> None:
    async with exposure_harness(tmp_path, stage="SHADOW") as h:
        await h.arm()
        result = value(await h.call("thread/input", "shadow-work", {"thread_id": h.thread}))
        assert h.model.guidance and set(h.model.guidance) == {"baseline-guidance"}
        execution = result["behavior_execution"]
        shadow = next(item for item in execution["snapshots"] if item["origin"] == "SHADOW")
        uses = [
            item
            for item in execution["uses"]
            if item["snapshot_digest"] == shadow["snapshot_digest"]
        ]
        assert uses and all(item["operation"] == "PROMPT_RENDER" for item in uses)
        current = value(
            await h.call(
                "improvement/exposure/runtime/read",
                "shadow-read",
                {
                    "exposure_id": h.exposure["spec"]["exposure_id"],
                },
            )
        )["exposure"]
        assert current["observations"][0]["decision_exposed"] is False
        assert current["observations"][0]["model_calls"] == 0


async def test_final_baseline_requires_separate_approval_and_preserves_research_baselines(
    tmp_path: Path,
) -> None:
    async with exposure_harness(tmp_path) as h:
        await h.arm()
        value(await h.call("thread/input", "trial", {"thread_id": h.thread}))
        identifier = h.exposure["spec"]["exposure_id"]
        current = value(
            await h.call(
                "improvement/exposure/runtime/read", "trial-read", {"exposure_id": identifier}
            )
        )["exposure"]
        completed = value(
            await h.call(
                "improvement/exposure/complete",
                "finish",
                {
                    "exposure_id": identifier,
                    "expected_revision": current["revision"],
                },
            )
        )["exposure"]
        assert completed["state"] == "COMPLETED"
        h.model.guidance.clear()
        value(await h.call("thread/input", "before-final-approval", {"thread_id": h.thread}))
        assert set(h.model.guidance) == {"baseline-guidance"}
        proposal = h.pair.proposal
        prepared = value(
            await h.call(
                "improvement/promotion/prepare",
                "final-preview",
                {
                    "improvement_revision_id": proposal["record_id"],
                    "assessment_refs": [h.pair.plan["record_id"]],
                    "candidate_digest": proposal["payload"]["candidate_digest"],
                    "baseline_digest": proposal["payload"]["baseline_digest"],
                    "target_scope": proposal["payload"]["scope_key"],
                    "policy_version": "fixture",
                },
            )
        )["promotion"]
        assert prepared["state"] == "PENDING"
        with h.pair.runtime.ledger.engine.connect() as connection:
            before = connection.execute(select(baseline_sets)).all()
        wrong = await h.call(
            "improvement/promotion/decide",
            "cannot-reuse-canary-approval",
            {
                "promotion_candidate_id": prepared["record_id"],
                "decision": "APPROVE",
                "approved_digest": h.exposure["spec"]["spec_digest"],
                "actor_ref": "human:policy-owner",
                "role_assignment_ref": h.role,
            },
        )
        assert wrong.error is not None
        applied = value(
            await h.call(
                "improvement/promotion/decide",
                "final-approval",
                {
                    "promotion_candidate_id": prepared["record_id"],
                    "decision": "APPROVE",
                    "approved_digest": prepared["record_digest"],
                    "actor_ref": "human:policy-owner",
                    "role_assignment_ref": h.role,
                },
            )
        )
        assert applied["current_state_mutated"] is True
        assert (
            applied["behavior_baseline_revision"]["content_digest"]
            == h.exposure["spec"]["candidate_digest"]
        )
        with h.pair.runtime.ledger.engine.connect() as connection:
            assert connection.execute(select(baseline_sets)).all() == before
        h.model.guidance.clear()
        result = value(await h.call("thread/input", "new-baseline", {"thread_id": h.thread}))
        assert set(h.model.guidance) == {"candidate-guidance"}
        snapshot = next(
            item
            for item in result["behavior_execution"]["snapshots"]
            if item["component"] == "PROMPT_BUNDLE"
        )
        assert snapshot["origin"] == "BASELINE" and snapshot["exposure_ref"] is None
        h.pair.runtime.close()
        h.pair.runtime = create_runtime(
            tmp_path,
            model_resolver=StaticModelResolver(h.model),
            connector_registry=ConnectorRegistry((h.connector,)),
            evaluation_catalog=FrozenEvaluationCatalog((h.pair.binding,)),
        )
        h.model.guidance.clear()
        value(await h.call("thread/input", "reopened-baseline", {"thread_id": h.thread}))
        assert set(h.model.guidance) == {"candidate-guidance"}
        current = value(
            await h.call(
                "improvement/exposure/runtime/read",
                "before-manual-rollback",
                {"exposure_id": identifier},
            )
        )["exposure"]
        restored = value(
            await h.call(
                "improvement/exposure/rollback",
                "restore-approved-baseline",
                {
                    "exposure_id": identifier,
                    "expected_revision": current["revision"],
                    "actor_ref": "human:policy-owner",
                    "role_assignment_ref": h.role,
                },
            )
        )
        assert restored["exposure"]["state"] == "ROLLED_BACK"
        h.model.guidance.clear()
        value(await h.call("thread/input", "restored-published-baseline", {"thread_id": h.thread}))
        assert set(h.model.guidance) == {"baseline-guidance"}
