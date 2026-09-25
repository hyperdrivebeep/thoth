from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from tests.integration.scoped_runtime import create_runtime, fixture_scope_policy
from tests.integration.test_a02_autonomous_acquisition import A02Connector, StaticModelResolver
from tests.integration.test_a04_r2_closed_loop import A04R2Model, request, value

from thoth.adapters.connectors import ConnectorRegistry
from thoth.apps.runtime import AppRuntime
from thoth.domain.sandbox import (
    SandboxExecutionState,
    SandboxResult,
    SandboxRunSpec,
    SandboxRuntimeProfile,
)
from thoth.domain.sandbox_capability import SandboxCapability
from thoth.ports.evaluation_runner import EvaluationCatalogPort
from thoth.ports.improvement import ImprovementEvaluatorPort
from thoth.ports.model import ModelPort


class RecoverySandboxAdapter:
    def __init__(self, results: tuple[tuple[SandboxExecutionState, str | None], ...]) -> None:
        self.results = deque(results)
        self.seen_specs: list[SandboxRunSpec] = []

    @property
    def capability(self) -> SandboxCapability:
        return SandboxCapability(
            runtime_profile=SandboxRuntimeProfile.SCRIPTED,
            available=True,
            runtime_version="recovery-test:1",
            security_tier="TEST_ONLY",
            network_modes=("DENY_ALL",),
        )

    async def run(self, spec: SandboxRunSpec) -> SandboxResult:
        self.seen_specs.append(spec)
        state, detail = self.results.popleft()
        now = datetime.now(UTC)
        return SandboxResult(
            project_id=spec.project_id,
            attempt_id=spec.attempt_id,
            runtime_profile=spec.runtime_profile,
            state=state,
            exit_code=0 if state == SandboxExecutionState.SUCCEEDED else 1,
            stdout="recovered" if state == SandboxExecutionState.SUCCEEDED else "",
            stderr="" if detail is None else detail,
            started_at=now,
            completed_at=now,
            cleanup_state=SandboxExecutionState.DESTROYED,
            failure_detail=detail,
        )

    async def cancel(self, attempt_id: str) -> bool:
        del attempt_id
        return False


def recovery_policy(max_retries: int) -> dict[str, object]:
    return {
        "resource_scope_policy": fixture_scope_policy().model_dump(mode="json"),
        "external_write": False,
        "physical_action": False,
        "unknown_action_tier": "R3",
        "connector_default": "DENY",
        "connector_allowlist": ["a02-readonly"],
        "connector_allowed_egress_classes": ["NONE"],
        "max_source_security_class": "RESTRICTED",
        "sandbox_runtime_allowlist": ["SCRIPTED"],
        "sandbox_network_policy": "DENY_ALL",
        "sandbox_allowed_hosts": [],
        "acquisition_routes": [],
        "counter_search_routes": [],
        "sandbox_action_templates": [
            {
                "action_family": "SANDBOX_REPLAY",
                "runtime_profile": "SCRIPTED",
                "image_digest": "scripted:a05",
                "argv": ["python", "-c", "print('bounded recovery')"],
                "network_policy": "DENY_ALL",
                "allowed_hosts": [],
            }
        ],
        "r2_recovery_policy": {
            "max_transient_retries": max_retries,
            "transient_markers": ["TRANSIENT_INFRA", "BOOT_FAILED", "TIMED_OUT"],
            "semantic_markers": ["SEMANTIC"],
            "code_markers": ["CODE"],
            "ambiguous_markers": ["AMBIGUOUS_EXTERNAL"],
        },
    }


async def prepare_recovery(
    tmp_path: Path,
    scenario: str,
    adapter: RecoverySandboxAdapter,
    *,
    max_retries: int,
    improvement_evaluator: ImprovementEvaluatorPort | None = None,
    evaluation_catalog: EvaluationCatalogPort | None = None,
) -> tuple[AppRuntime, str, str]:
    connector = A02Connector()
    runtime = create_runtime(
        tmp_path / scenario,
        connector_registry=ConnectorRegistry((connector,)),
        sandbox_adapter=adapter,
        model_resolver=StaticModelResolver(cast(ModelPort, A04R2Model())),
        improvement_evaluator=improvement_evaluator,
        evaluation_catalog=evaluation_catalog,
    )
    project_id = f"project:a05:{scenario}"
    thread_id = f"thread:a05:{scenario}"
    value(
        await runtime.bus.dispatch(
            request(
                "project/create",
                f"a05-{scenario}-project",
                {
                    "project_id": project_id,
                    "name": f"A05 {scenario}",
                    "cutoff_at": "2026-09-01T00:00:00Z",
                },
            )
        )
    )
    value(
        await runtime.bus.dispatch(
            request(
                "project/policy/update",
                f"a05-{scenario}-policy",
                {
                    "project_id": project_id,
                    "expected_revision": 0,
                    "payload": recovery_policy(max_retries),
                },
            )
        )
    )
    value(
        await runtime.bus.dispatch(
            request(
                "project/source/connect",
                f"a05-{scenario}-source",
                {
                    "project_id": project_id,
                    "connector_id": "a02-readonly",
                    "selector": {"relative_path": "initial.md"},
                    "media_type": "text/markdown",
                    "authority": "OFFICIAL",
                    "cutoff_state": "ELIGIBLE",
                    "security_class": "INTERNAL",
                },
            )
        )
    )
    value(
        await runtime.bus.dispatch(
            request(
                "thread/start",
                f"a05-{scenario}-thread",
                {
                    "project_id": project_id,
                    "thread_id": thread_id,
                    "problem": "Run a bounded R2 action with typed recovery.",
                    "scope": {"workstream": "bounded-recovery"},
                },
            )
        )
    )
    return runtime, project_id, thread_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "results", "max_retries", "terminal", "attempts"),
    (
        (
            "transient",
            (
                (SandboxExecutionState.BOOT_FAILED, "TRANSIENT_INFRA: boot race"),
                (SandboxExecutionState.SUCCEEDED, None),
            ),
            1,
            "COMPLETED",
            2,
        ),
        (
            "semantic",
            ((SandboxExecutionState.FAILED, "SEMANTIC: invalid assumption"),),
            2,
            "CANDIDATE_REVISION_CREATED",
            1,
        ),
        (
            "code",
            ((SandboxExecutionState.FAILED, "CODE: deterministic syntax failure"),),
            2,
            "CANDIDATE_REVISION_CREATED",
            1,
        ),
        (
            "ambiguous",
            ((SandboxExecutionState.FAILED, "AMBIGUOUS_EXTERNAL: result unknown"),),
            2,
            "RECONCILIATION_REQUIRED",
            1,
        ),
        (
            "budget",
            (
                (SandboxExecutionState.BOOT_FAILED, "TRANSIENT_INFRA: one"),
                (SandboxExecutionState.BOOT_FAILED, "TRANSIENT_INFRA: two"),
            ),
            1,
            "RETRY_BUDGET_EXHAUSTED",
            2,
        ),
    ),
)
async def test_thread_input_applies_typed_bounded_recovery(
    tmp_path: Path,
    scenario: str,
    results: tuple[tuple[SandboxExecutionState, str | None], ...],
    max_retries: int,
    terminal: str,
    attempts: int,
) -> None:
    adapter = RecoverySandboxAdapter(results)
    runtime, project_id, thread_id = await prepare_recovery(
        tmp_path,
        scenario,
        adapter,
        max_retries=max_retries,
    )
    try:
        analyzed = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    f"a05-{scenario}-input",
                    {"project_id": project_id, "thread_id": thread_id},
                )
            )
        )
    finally:
        runtime.close()

    closed_loop = cast(dict[str, JsonValue], analyzed["r2_closed_loop"])
    recovery = cast(dict[str, JsonValue], closed_loop["recovery"])
    assert closed_loop["terminal_state"] == terminal
    assert len(adapter.seen_specs) == attempts
    assert len({item.attempt_id for item in adapter.seen_specs}) == attempts
    if scenario != "transient":
        assert recovery["baseline_head_before"] == recovery["baseline_head_after"]
    receipt = cast(dict[str, JsonValue], recovery["failure_receipt"])
    assert receipt["semantic_truth_certified"] is False
    if scenario in {"semantic", "code"}:
        assert cast(list[str], recovery["candidate_branch_revision_ids"])
        assert recovery["repair_basis"]
    elif scenario == "ambiguous":
        reconciliation = cast(dict[str, JsonValue], recovery["reconciliation"])
        assert reconciliation["state"] == "REQUIRED"
        assert reconciliation["automatic_retry_allowed"] is False
    elif scenario == "budget":
        assert recovery["retry_budget_exhausted"] is True
