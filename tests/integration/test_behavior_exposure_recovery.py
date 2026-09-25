from pathlib import Path

import pytest
from tests.integration.behavior_exposure_helpers import exposure_harness
from tests.integration.storage_coverage_helpers import value
from tests.integration.test_a02_autonomous_acquisition import StaticModelResolver, policy_payload

from thoth.adapters.connectors import ConnectorRegistry
from thoth.adapters.evaluators.catalog import FrozenEvaluationCatalog
from thoth.application.commands.behavior_execution import BehaviorThreadEntry
from thoth.apps.runtime import create_runtime
from thoth.domain.behavior_execution import BehaviorExecutionRecord


async def test_unknown_finalization_after_reopen_uses_baseline_without_replaying_canary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with exposure_harness(tmp_path) as h:
        await h.arm()
        original = BehaviorThreadEntry._persist  # pyright: ignore[reportPrivateUsage]

        def fail_final(entry: BehaviorThreadEntry, record: BehaviorExecutionRecord) -> None:
            original(entry, record)
            if record.state == "COMPLETED":
                raise RuntimeError("injected loss before final behavior transaction commits")

        with monkeypatch.context() as patch:
            patch.setattr(BehaviorThreadEntry, "_persist", fail_final)
            failed = await h.call("thread/input", "unknown-finalization", {"thread_id": h.thread})
            assert failed.error is not None
        h.pair.runtime.close()
        h.pair.runtime = create_runtime(
            tmp_path,
            model_resolver=StaticModelResolver(h.model),
            connector_registry=ConnectorRegistry((h.connector,)),
            evaluation_catalog=FrozenEvaluationCatalog((h.pair.binding,)),
        )
        h.model.guidance.clear()
        resumed = value(
            await h.call("thread/input", "new-work-after-reopen", {"thread_id": h.thread})
        )
        assert set(h.model.guidance) == {"baseline-guidance"}
        snapshot = next(
            item
            for item in resumed["behavior_execution"]["snapshots"]
            if item["component"] == "PROMPT_BUNDLE"
        )
        assert snapshot["reason_code"] == "BEHAVIOR_PRIOR_WORK_UNRESOLVED"
        exposure = value(
            await h.call(
                "improvement/exposure/runtime/read",
                "unknown-read",
                {
                    "exposure_id": h.exposure["spec"]["exposure_id"],
                },
            )
        )["exposure"]
        assert exposure["request_count"] == 1 and exposure["observations"] == []


async def test_another_project_does_not_consume_or_spend_the_canary(tmp_path: Path) -> None:
    async with exposure_harness(tmp_path) as h:
        await h.arm()
        other = "project:other-policy"
        # call() merges the explicit project last, so this is a separate project in the same DB.
        created = value(
            await h.call(
                "project/create",
                "other-project",
                {
                    "project_id": other,
                    "name": "Other policy",
                    "cutoff_at": "2026-09-01T00:00:00Z",
                },
            )
        )
        value(
            await h.call(
                "project/policy/update",
                "other-policy",
                {
                    "project_id": other,
                    "expected_revision": created["revision"],
                    "payload": policy_payload(allow_connector=True),
                },
            )
        )
        for name in ("initial.md", "catalog.md"):
            value(
                await h.call(
                    "project/source/connect",
                    "other-" + name,
                    {
                        "project_id": other,
                        "connector_id": "a02-readonly",
                        "selector": {"relative_path": name},
                        "media_type": "text/markdown",
                        "authority": "OFFICIAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                )
            )
        value(
            await h.call(
                "thread/start",
                "other-thread",
                {
                    "project_id": other,
                    "thread_id": "thread:other-policy",
                    "problem": "Which dataset_version produced the result?",
                    "scope": {"workstream": "evaluation"},
                },
            )
        )
        h.model.guidance.clear()
        result = value(
            await h.call(
                "thread/input",
                "other-input",
                {"project_id": other, "thread_id": "thread:other-policy"},
            )
        )
        assert h.model.guidance and set(h.model.guidance) == {None}
        assert all(
            item["origin"] == "BUILTIN_DEFAULT"
            for item in result["behavior_execution"]["snapshots"]
        )
        exposure = value(
            await h.call(
                "improvement/exposure/runtime/read",
                "unchanged-canary",
                {
                    "exposure_id": h.exposure["spec"]["exposure_id"],
                },
            )
        )["exposure"]
        assert exposure["state"] == "ARMED" and exposure["request_count"] == 0
