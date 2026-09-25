from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from tests.integration.storage_coverage_helpers import domain_snapshot, request, value
from tests.integration.test_a02_autonomous_acquisition import policy_payload as acquisition_policy
from tests.integration.test_a04_r2_closed_loop import (
    policy_payload as r2_policy,
)
from tests.integration.test_a04_r2_closed_loop import (
    prepare_a04,
)
from tests.integration.test_a04_r2_closed_loop import (
    test_thread_input_holds_r2_result_when_head_changes_during_sandbox_io as run_stale_scenario,
)
from tests.integration.test_connector_sandbox_cycle import (
    test_connector_receipt_and_r2_sandbox_auto_completion as run_public_sandbox,
)

from thoth.adapters.sandbox import ScriptedSandboxAdapter
from thoth.adapters.storage import ContentAddressedObjectStore
from thoth.adapters.storage.artifacts import SqliteArtifactLedger
from thoth.adapters.storage.operations import SqliteOperationStore
from thoth.adapters.storage.threads import SqliteThreadStore
from thoth.application.services.r2_sandbox_compiler import R2SandboxSpecCompiler
from thoth.application.services.sandbox_service import SandboxExecutionBundle, SandboxService
from thoth.apps.runtime import create_runtime
from thoth.domain.action import ActionCandidate, ActionPlan
from thoth.domain.canonical import head_set_digest
from thoth.domain.enums import CutoffState
from thoth.domain.project import Project
from thoth.domain.sandbox import SandboxResult, SandboxRunSpec
from thoth.protocol.jsonrpc import RpcErrorCode


@pytest.mark.parametrize("fails", [False, True])
async def test_terminal_sandbox_attempt_releases_adapter_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fails: bool,
) -> None:
    class RaisingSandbox(ScriptedSandboxAdapter):
        async def run(self, spec: SandboxRunSpec) -> SandboxResult:
            self.seen_specs.append(spec)
            raise RuntimeError("injected adapter failure")

    sandbox = RaisingSandbox() if fails else ScriptedSandboxAdapter()
    runtime, _, project, thread = await prepare_a04(tmp_path, allow_sandbox=True, sandbox=sandbox)
    original = SandboxService.run
    retained: list[int] = []

    async def observe(service: SandboxService, spec: SandboxRunSpec) -> SandboxExecutionBundle:
        try:
            return await original(service, spec)
        finally:
            retained.append(len(service._attempt_adapters))  # pyright: ignore[reportPrivateUsage]

    try:
        monkeypatch.setattr(SandboxService, "run", observe)
        response = await runtime.bus.dispatch(
            request(
                "thread/input",
                "retention",
                {
                    "project_id": project,
                    "thread_id": thread,
                },
            )
        )
        assert bool(response.error) is fails
        assert len(sandbox.seen_specs) == 1
        assert retained == [0]
    finally:
        runtime.close()


async def test_authorized_acquisition_can_continue_into_same_thread_r2_loop(tmp_path: Path) -> None:
    runtime, sandbox, project, _ = await prepare_a04(tmp_path, allow_sandbox=True)
    try:
        policy = r2_policy(allow_sandbox=True)
        policy["acquisition_routes"] = acquisition_policy(allow_connector=True)[
            "acquisition_routes"
        ]
        current = value(
            await runtime.bus.dispatch(
                request(
                    "project/read",
                    "read-policy-basis",
                    {
                        "project_id": project,
                    },
                )
            )
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "project/policy/update",
                    "acquisition-policy",
                    {
                        "project_id": project,
                        "expected_revision": current["revision"],
                        "payload": policy,
                    },
                )
            )
        )
        thread = "thread:acquire-then-r2"
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "combined-thread",
                    {
                        "project_id": project,
                        "thread_id": thread,
                        "problem": "Find dataset_version, then run the bounded check.",
                    },
                )
            )
        )
        result = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "combined-input",
                    {
                        "project_id": project,
                        "thread_id": thread,
                    },
                )
            )
        )
        assert result["autonomous_acquisition"]["terminal_state"] == "SUFFICIENT"
        assert result["r2_closed_loop"]["terminal_state"] == "COMPLETED"
        assert len(sandbox.seen_specs) == 1
    finally:
        runtime.close()


async def test_stale_project_spec_denied_before_object_bytes_or_sandbox_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, sandbox, project, thread = await prepare_a04(tmp_path, allow_sandbox=True)
    original_compile = R2SandboxSpecCompiler.compile
    original_read = ContentAddressedObjectStore.read
    reads = 0

    def stale_compile(
        compiler: R2SandboxSpecCompiler,
        *,
        project: Project,
        plan: ActionPlan,
        action: ActionCandidate,
    ) -> SandboxRunSpec:
        spec = original_compile(compiler, project=project, plan=plan, action=action)
        assert spec.project_revision is not None and spec.project_revision > 0
        return spec.model_copy(update={"project_revision": spec.project_revision - 1})

    def observe_read(store: ContentAddressedObjectStore, digest: str) -> bytes:
        nonlocal reads
        reads += 1
        return original_read(store, digest)

    try:
        monkeypatch.setattr(R2SandboxSpecCompiler, "compile", stale_compile)
        monkeypatch.setattr(ContentAddressedObjectStore, "read", observe_read)
        result = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "stale-project",
                    {
                        "project_id": project,
                        "thread_id": thread,
                    },
                )
            )
        )
        assert result["r2_closed_loop"]["terminal_state"] == "HOLD"
        assert reads == 0
        assert sandbox.seen_specs == []
    finally:
        runtime.close()


@pytest.mark.parametrize("consumer", ["outcome", "execution"])
async def test_rejected_sandbox_result_cannot_reenter_other_consumers(
    tmp_path: Path,
    consumer: str,
) -> None:
    await run_stale_scenario(tmp_path)
    runtime = create_runtime(tmp_path / "allowed")
    project = "project:a04:allowed"
    try:
        artifacts = SqliteArtifactLedger(runtime.ledger.engine)
        sandbox_ids = {
            item.artifact_id
            for item in artifacts.list_artifacts(project)
            if item.source_uri.startswith("sandbox://")
        }
        all_spans = artifacts.list_evidence(project)
        stale_refs = [span.span_id for span in all_spans if span.artifact_id in sandbox_ids]
        safe_refs = [span.span_id for span in all_spans if span.artifact_id not in sandbox_ids]
        assert stale_refs and safe_refs
        thread = SqliteThreadStore(runtime.ledger.engine).read("thread:a04:allowed")
        assert thread is not None
        object_id = thread.current_object_ids[0]
        action = value(
            await runtime.bus.dispatch(
                request(
                    "action/create",
                    "safe-action",
                    {
                        "project_id": project,
                        "object_id": object_id,
                        "primary_purpose": "INFORMATION_ACQUISITION",
                        "evidence_refs": safe_refs,
                        "specification": {
                            "description": "Read-only local metadata fixture",
                            "expected_observation_or_change": {
                                "description": "read target state"
                            },
                            "effect_completeness_confirmed": True,
                            "stop_conditions": ["read complete"],
                            "observability": "receipt",
                            "effect_vector": {
                                "effect_completeness_confirmed": True,
                                "external_write": False,
                            },
                        },
                    },
                )
            )
        )["action"]
        plan = value(
            await runtime.bus.dispatch(
                request(
                    "action/plan/compose",
                    "safe-plan",
                    {
                        "project_id": project,
                        "object_id": object_id,
                        "selected_action_refs": [action["action_id"]],
                        "dependency_edges": [],
                        "step_candidates": [
                            {
                                "step_id": "step:read",
                                "action_ref": action["action_id"],
                                "inputs": safe_refs,
                                "output_contract": {"type": "observation"},
                                "preconditions": [],
                                "stop_conditions": ["read complete"],
                                "state": "READY",
                                "effect_vector": {
                                    "effect_completeness_confirmed": True,
                                    "external_write": False,
                                },
                            }
                        ],
                    },
                )
            )
        )["plan"]
        if consumer == "outcome":
            series = value(
                await runtime.bus.dispatch(
                    request(
                        "outcome/series/create",
                        "series",
                        {
                            "project_id": project,
                            "object_id": object_id,
                            "action_plan_revision_digest": plan["revision_digest"],
                            "profile_ref": "EXPERIMENT_LEARNING_OUTCOME",
                            "comparison_baseline_set_digest": "a" * 64,
                            "assessment_windows": [
                                {"assessment_phase": "INTERIM", "window": "local replay"}
                            ],
                        },
                    )
                )
            )["series"]
            assert series["phase_states"] == {"INTERIM": "WAITING_OBSERVATION"}
            rejected = request(
                "outcome/observation/link",
                "stale-link",
                {
                    "project_id": project,
                    "outcome_series_id": series["outcome_series_id"],
                    "assessment_phase": "INTERIM",
                    "observation_refs": stale_refs,
                    "completeness": "PARTIAL",
                    "evidence_refs": safe_refs,
                    "expected_series_revision": series["revision"],
                },
            )
        else:
            started = value(
                await runtime.bus.dispatch(
                    request(
                        "execution/start",
                        "safe-start",
                        {
                            "project_id": project,
                            "plan_id": plan["plan_id"],
                            "plan_revision_digest": plan["revision_digest"],
                            "execution_profile_ref": "local",
                            "expected_working_head_digest": head_set_digest(
                                runtime.ledger.read_heads(project)
                            ),
                        },
                    )
                )
            )
            attempt = started["attempts"][0]
            completed = value(
                await runtime.bus.dispatch(
                    request(
                        "execution/reconcile",
                        "safe-complete",
                        {
                            "project_id": project,
                            "plan_execution_id": started["execution"]["plan_execution_id"],
                            "attempt_id": attempt["attempt_id"],
                            "target_state_evidence_refs": safe_refs,
                            "reconciliation_method": "TARGET_CHECK_CONFIRMED_APPLIED",
                        },
                    )
                )
            )
            rejected = request(
                "execution/observation/link",
                "stale-link",
                {
                    "project_id": project,
                    "attempt_id": attempt["attempt_id"],
                    "observation_refs": stale_refs,
                    "completeness": "PARTIAL",
                    "evidence_refs": safe_refs,
                    "expected_execution_revision": completed["execution"]["revision"],
                },
            )
        before = domain_snapshot(runtime.ledger.engine)
        response = await runtime.bus.dispatch(rejected)
        assert response.error is not None and response.error.code == RpcErrorCode.DOMAIN_REJECTED
        assert "ineligible evidence" in response.error.message
        assert domain_snapshot(runtime.ledger.engine) == before
    finally:
        runtime.close()


async def test_rejected_sandbox_result_cannot_reenter_action_after_restart(tmp_path: Path) -> None:
    await run_stale_scenario(tmp_path)
    runtime = create_runtime(tmp_path / "allowed")
    project = "project:a04:allowed"
    try:
        artifacts = SqliteArtifactLedger(runtime.ledger.engine)
        result_ids = {
            item.artifact_id
            for item in artifacts.list_artifacts(project)
            if item.source_uri.startswith("sandbox://")
        }
        spans = tuple(
            span for span in artifacts.list_evidence(project) if span.artifact_id in result_ids
        )
        assert spans
        thread = SqliteThreadStore(runtime.ledger.engine).read("thread:a04:allowed")
        assert thread is not None
        before = domain_snapshot(runtime.ledger.engine)
        response = await runtime.bus.dispatch(
            request(
                "action/create",
                "stale-observation",
                {
                    "project_id": project,
                    "object_id": thread.current_object_ids[0],
                    "primary_purpose": "INFORMATION_ACQUISITION",
                    "specification": {
                        "description": "Use the prior sandbox observation",
                        "expected_observation_or_change": {
                            "description": "compare the observation"
                        },
                        "effect_completeness_confirmed": True,
                        "stop_conditions": ["comparison done"],
                        "observability": "read receipt",
                        "effect_vector": {
                            "effect_completeness_confirmed": True,
                            "external_write": False,
                        },
                    },
                    "evidence_refs": [span.span_id for span in spans],
                },
            )
        )
        assert response.error is not None
        assert response.error.code == RpcErrorCode.DOMAIN_REJECTED
        assert all(span.cutoff_state == CutoffState.PROHIBITED_CONTEXT for span in spans)
        assert domain_snapshot(runtime.ledger.engine) == before
    finally:
        runtime.close()


async def test_held_result_artifact_cannot_be_laundered_through_new_sandbox_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await run_stale_scenario(tmp_path)
    sandbox = ScriptedSandboxAdapter()
    runtime = create_runtime(tmp_path / "allowed", sandbox_adapter=sandbox)
    project = "project:a04:allowed"
    reads = 0
    original_read = ContentAddressedObjectStore.read

    def observe_read(store: ContentAddressedObjectStore, digest: str) -> bytes:
        nonlocal reads
        reads += 1
        return original_read(store, digest)

    try:
        artifacts = SqliteArtifactLedger(runtime.ledger.engine)
        held = next(
            item
            for item in artifacts.list_artifacts(project)
            if item.source_uri.startswith("sandbox://")
        )
        thread = SqliteThreadStore(runtime.ledger.engine).read("thread:a04:allowed")
        assert thread is not None
        policy = value(
            await runtime.bus.dispatch(
                request(
                    "project/policy/read",
                    "input-policy",
                    {
                        "project_id": project,
                    },
                )
            )
        )["policy"]
        effect = {
            "effect_completeness_confirmed": True,
            "runs_untrusted_code": True,
            "external_write": False,
        }
        action = value(
            await runtime.bus.dispatch(
                request(
                    "action/create",
                    "launder-action",
                    {
                        "project_id": project,
                        "object_id": thread.current_object_ids[0],
                        "primary_purpose": "ANALYSIS_COMPUTATION",
                        "evidence_refs": [],
                        "specification": {
                            "description": "Compute using an input snapshot",
                            "expected_observation_or_change": {
                                "description": "derived observation"
                            },
                            "effect_completeness_confirmed": True,
                            "stop_conditions": ["result received"],
                            "observability": "receipt",
                            "effect_vector": effect,
                        },
                    },
                )
            )
        )["action"]
        plan = value(
            await runtime.bus.dispatch(
                request(
                    "action/plan/compose",
                    "launder-plan",
                    {
                        "project_id": project,
                        "object_id": thread.current_object_ids[0],
                        "selected_action_refs": [action["action_id"]],
                        "dependency_edges": [],
                        "step_candidates": [
                            {
                                "step_id": "step:launder",
                                "action_ref": action["action_id"],
                                "inputs": [],
                                "output_contract": {"type": "observation"},
                                "preconditions": [],
                                "stop_conditions": ["result received"],
                                "state": "READY",
                                "effect_vector": effect,
                                "sandbox_spec": {
                                    "runtime_profile": "SCRIPTED",
                                    "image_digest": "scripted:image",
                                    "argv": ["python", "-c", "print(1)"],
                                    "policy_id": policy["policy_id"],
                                    "policy_revision": policy["version"],
                                    "policy_digest": policy["policy_digest"],
                                    "input_snapshots": [
                                        {
                                            "artifact_id": held.artifact_id,
                                            "content_sha256": held.byte_sha256,
                                            "source_path": "ledger-bound",
                                            "target_name": "prior-result.json",
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                )
            )
        )["plan"]
        before = domain_snapshot(runtime.ledger.engine)
        monkeypatch.setattr(ContentAddressedObjectStore, "read", observe_read)
        response = await runtime.bus.dispatch(
            request(
                "execution/start",
                "launder-start",
                {
                    "project_id": project,
                    "plan_id": plan["plan_id"],
                    "plan_revision_digest": plan["revision_digest"],
                    "execution_profile_ref": "local",
                    "expected_working_head_digest": head_set_digest(
                        runtime.ledger.read_heads(project)
                    ),
                },
            )
        )
        assert response.error is not None and response.error.code == RpcErrorCode.DOMAIN_REJECTED
        assert reads == 0 and sandbox.seen_specs == []
        assert "ineligible" in response.error.message
        assert held.cutoff_state == CutoffState.PROHIBITED_CONTEXT
        assert domain_snapshot(runtime.ledger.engine) == before
    finally:
        runtime.close()


async def test_success_response_matches_admitted_artifact_after_restart(tmp_path: Path) -> None:
    await run_public_sandbox(tmp_path)
    runtime = create_runtime(tmp_path / "workspace")
    try:
        completed = next(
            item
            for item in SqliteOperationStore(runtime.ledger.engine).list_by_project(
                "project:connector-sandbox"
            )
            if item.method == "execution/start"
        )
        assert completed.result is not None
        result = cast(dict[str, Any], completed.result)
        returned = result["sandbox_runs"][0]["observation_artifact"]
        artifact = SqliteArtifactLedger(runtime.ledger.engine).read_artifact(
            returned["artifact_id"]
        )
        assert artifact is not None and artifact.cutoff_state == CutoffState.ELIGIBLE
        assert returned == artifact.model_dump(mode="json")
        scope = value(
            await runtime.bus.dispatch(
                request(
                    "project/source/scope/read",
                    "independent-output-owner",
                    {
                        "project_id": "project:connector-sandbox",
                        "resource_ref": returned["artifact_id"],
                    },
                )
            )
        )["scope"]
        assert scope["access_basis"] == "SOURCE_OWNER"
        assert scope["owner_kind"] == "PROJECT"
    finally:
        runtime.close()
