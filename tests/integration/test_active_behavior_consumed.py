"""N06: normal Thread execution must consume the actual baseline policy contents."""

from dataclasses import replace
from pathlib import Path
from typing import TypeVar, cast

import pytest
from pydantic import BaseModel
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a02_autonomous_acquisition import DynamicA02Model, prepare_thread
from tests.integration.test_normal_public_research_identity import capture_contexts

from thoth.adapters.runtime import SystemClock
from thoth.adapters.storage.behavior_artifact import SqliteBehaviorArtifactStore
from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.adapters.storage.threads import SqliteThreadStore
from thoth.apps.runtime import AppRuntime
from thoth.domain.action import ActionPlanDraft
from thoth.domain.behavior_artifact import (
    BehaviorArtifact,
    BehaviorArtifactKind,
    BehaviorArtifactState,
    BehaviorRegistryEntry,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ModelRole, ThreadExecutionState
from thoth.domain.model import ModelRequest, ModelResult

TModel = TypeVar("TModel", bound=BaseModel)


def install_baseline(
    runtime: AppRuntime, project: str, kind: BehaviorArtifactKind, values: dict[str, object]
) -> BehaviorArtifact:
    content = {"kind": kind.value, "version": "2.0.0", **values}
    artifact = BehaviorArtifact(
        artifact_id=f"behavior:host:{kind.value}",
        project_id=project,
        kind=kind,
        version="2.0.0",
        state=BehaviorArtifactState.BASELINE,
        content=content,
        content_digest=domain_digest(
            "BEHAVIOR_ARTIFACT_CONTENT", "1.0.0", canonical_payload(content)
        ),
        created_at=SystemClock().now(),
    )
    SqliteBehaviorArtifactStore(runtime.ledger.engine).add(artifact)
    return artifact


async def test_normal_thread_consumes_baseline_prompt_content_and_reports_its_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contexts = capture_contexts(monkeypatch)
    runtime, _connector, project = await prepare_thread(tmp_path, allow_connector=True)
    guidance = "State the source before each claim."
    content: dict[str, object] = {
        "kind": "PROMPT_BUNDLE",
        "version": "2.0.0",
        "task_guidance": guidance,
    }
    digest = domain_digest("BEHAVIOR_ARTIFACT_CONTENT", "1.0.0", canonical_payload(content))
    try:
        # Host-installed baseline fixture, not an unapproved candidate activation.
        SqliteBehaviorArtifactStore(runtime.ledger.engine).add(
            BehaviorArtifact(
                artifact_id="behavior:host-prompt-baseline",
                project_id=project,
                kind=BehaviorArtifactKind.PROMPT_BUNDLE,
                version="2.0.0",
                state=BehaviorArtifactState.BASELINE,
                content=content,
                content_digest=digest,
                created_at=SystemClock().now(),
            )
        )
        result = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "consume-baseline",
                    {
                        "project_id": project,
                        "thread_id": f"thread:{project}",
                    },
                )
            )
        )
        assert contexts and all(
            context["policy_hints"].get("behavior_guidance") == guidance for context in contexts
        ), "normal model inputs did not consume the stored baseline policy"
        execution = result["behavior_execution"]
        assert execution["state"] == "COMPLETED"
        assert any(item["content_digest"] == digest for item in execution["snapshots"])
        assert any(item["component"] == "PROMPT_BUNDLE" for item in execution["uses"])
    finally:
        runtime.close()


async def test_missing_and_queued_threads_do_not_create_behavior_execution(tmp_path: Path) -> None:
    runtime, _connector, project = await prepare_thread(tmp_path, allow_connector=True)
    try:
        records = SqliteControlRecordStore(runtime.ledger.engine)
        before = records.list(project, "BEHAVIOR_RUNTIME")
        invalid = await runtime.bus.dispatch(
            request(
                "thread/input",
                "missing-target",
                {
                    "project_id": project,
                    "thread_id": "thread:missing",
                },
            )
        )
        assert (
            invalid.error is not None
            and invalid.error.message == "thread was not found in this project"
        )
        assert records.list(project, "BEHAVIOR_RUNTIME") == before
        threads = SqliteThreadStore(runtime.ledger.engine)
        current = threads.read(f"thread:{project}")
        assert current is not None
        assert threads.update(
            current.model_copy(
                update={
                    "execution_state": ThreadExecutionState.RUNNING,
                    "revision": current.revision + 1,
                }
            ),
            expected_revision=current.revision,
        )
        queued = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "queue-only",
                    {
                        "project_id": project,
                        "thread_id": current.thread_id,
                        "instruction": "Queue this next check.",
                    },
                )
            )
        )
        assert queued["queued"] is True and "behavior_execution" not in queued
        assert records.list(project, "BEHAVIOR_RUNTIME") == before
    finally:
        runtime.close()


async def test_normal_retrieval_consumes_the_registered_span_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contexts = capture_contexts(monkeypatch)
    runtime, connector, project = await prepare_thread(tmp_path, allow_connector=True)
    try:
        connector.payloads["extra.md"] = b"# Additional\n\nOther report details.\n"
        for name in ("catalog.md", "extra.md"):
            value(
                await runtime.bus.dispatch(
                    request(
                        "project/source/connect",
                        "connect-" + name,
                        {
                            "project_id": project,
                            "connector_id": "a02-readonly",
                            "selector": {"relative_path": name},
                            "media_type": "text/markdown",
                            "authority": "OFFICIAL",
                            "cutoff_state": "ELIGIBLE",
                            "security_class": "INTERNAL",
                        },
                    )
                )
            )
        baseline = install_baseline(
            runtime,
            project,
            BehaviorArtifactKind.RETRIEVAL_POLICY,
            {
                "max_spans": 1,
                "character_budget": 50000,
            },
        )
        result = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "bounded-retrieval",
                    {
                        "project_id": project,
                        "thread_id": f"thread:{project}",
                    },
                )
            )
        )
        assert contexts and all(len(context["evidence"]) == 1 for context in contexts)
        assert result["selected_evidence_count"] == 1
        execution = result["behavior_execution"]
        selected = next(
            item for item in execution["snapshots"] if item["component"] == "RETRIEVAL_POLICY"
        )
        assert selected["content_digest"] == baseline.content_digest
        assert any(item["operation"] == "EVIDENCE_SELECTION" for item in execution["uses"])
    finally:
        runtime.close()


@pytest.mark.parametrize("repairs", [0, 1])
async def test_normal_workflow_consumes_the_registered_semantic_repair_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repairs: int
) -> None:
    original = DynamicA02Model.structured
    planner_prompts: list[str] = []

    async def first_invalid(
        model: DynamicA02Model, request: ModelRequest[TModel]
    ) -> ModelResult[TModel]:
        result = await original(model, request)
        if request.role == ModelRole.ACTION_PLANNER:
            planner_prompts.append(request.prompt_version)
            if len(planner_prompts) == 1:
                draft = cast(ActionPlanDraft, result.output)
                invalid = draft.model_copy(update={"alternatives": (), "proposed_frontier": ()})
                return replace(result, output=cast(TModel, invalid))
        return result

    monkeypatch.setattr(DynamicA02Model, "structured", first_invalid)
    runtime, _connector, project = await prepare_thread(tmp_path, allow_connector=True)
    try:
        install_baseline(
            runtime,
            project,
            BehaviorArtifactKind.WORKFLOW_DEFINITION,
            {
                "max_semantic_repairs": repairs,
            },
        )
        response = await runtime.bus.dispatch(
            request(
                "thread/input",
                "workflow-policy",
                {
                    "project_id": project,
                    "thread_id": f"thread:{project}",
                },
            )
        )
        repair_calls = [prompt for prompt in planner_prompts if "semantic_repair" in prompt]
        if repairs == 0:
            assert response.error is not None
            assert "BEHAVIOR_SEMANTIC_REPAIR_DISABLED" in response.error.message
            assert len(planner_prompts) == 1 and repair_calls == []
        else:
            result = value(response)
            assert len(repair_calls) == 1
            assert any(
                item["operation"] == "WORKFLOW_POLICY"
                for item in result["behavior_execution"]["uses"]
            )
    finally:
        runtime.close()


async def test_unapproved_legacy_active_pointer_does_not_change_normal_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contexts = capture_contexts(monkeypatch)
    runtime, _connector, project = await prepare_thread(tmp_path, allow_connector=True)
    try:
        baseline = install_baseline(
            runtime,
            project,
            BehaviorArtifactKind.PROMPT_BUNDLE,
            {
                "task_guidance": "Use the approved baseline guidance.",
            },
        )
        content: dict[str, object] = {
            "kind": "PROMPT_BUNDLE",
            "version": "2.0.0",
            "task_guidance": "UNAPPROVED OVERRIDE",
        }
        candidate = BehaviorArtifact(
            artifact_id="behavior:unapproved",
            project_id=project,
            kind=BehaviorArtifactKind.PROMPT_BUNDLE,
            version="2.0.0",
            state=BehaviorArtifactState.CANDIDATE,
            content=content,
            content_digest=domain_digest(
                "BEHAVIOR_ARTIFACT_CONTENT", "1.0.0", canonical_payload(content)
            ),
            created_at=SystemClock().now(),
        )
        store = SqliteBehaviorArtifactStore(runtime.ledger.engine)
        store.add(candidate)
        store.activate(
            candidate,
            BehaviorRegistryEntry(
                project_id=project,
                kind=candidate.kind,
                active_artifact_id=candidate.artifact_id,
                active_digest=candidate.content_digest,
                baseline_digest=baseline.content_digest,
                revision=1,
                updated_at=SystemClock().now(),
            ),
        )
        result = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "unapproved-pointer",
                    {
                        "project_id": project,
                        "thread_id": f"thread:{project}",
                    },
                )
            )
        )
        assert contexts and all(
            context["policy_hints"]["behavior_guidance"] == baseline.content["task_guidance"]
            for context in contexts
        )
        snapshot = next(
            item
            for item in result["behavior_execution"]["snapshots"]
            if item["component"] == "PROMPT_BUNDLE"
        )
        assert (
            snapshot["origin"] == "BASELINE"
            and snapshot["content_digest"] == baseline.content_digest
        )
        assert snapshot["ignored_registry_digest"] == candidate.content_digest
        assert snapshot["reason_code"] == "LEGACY_POINTER_NOT_EXECUTABLE"
    finally:
        runtime.close()
