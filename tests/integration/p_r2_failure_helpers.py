"""Normal-entry R2 fault instrumentation; snapshots use independent connections."""

from __future__ import annotations

from typing import Any

import pytest
from tests.atomicity.harness import snapshot
from tests.integration.test_hypothesis_prediction_outcome_cycle import request, value

from thoth.adapters.storage import transaction as transaction_module
from thoth.application.services.r2_closed_loop import R2ClosedLoopCoordinator
from thoth.apps.runtime import AppRuntime
from thoth.domain.sandbox import SandboxRunSpec


def ambient() -> bool:
    return vars(transaction_module)["_AMBIENT"].get() is not None


def capture_exit(patch: pytest.MonkeyPatch, runtime: AppRuntime, evidence: dict[str, Any]) -> None:
    original = R2ClosedLoopCoordinator.execute_if_required

    async def execute(coordinator: R2ClosedLoopCoordinator, **kwargs: Any) -> Any:
        try:
            return await original(coordinator, **kwargs)
        finally:
            # This runs before the outer handler publishes its terminal operation/error.
            evidence["after_phase"] = snapshot(runtime.ledger.engine)
            evidence["ambient_after_phase"] = ambient()

    patch.setattr(R2ClosedLoopCoordinator, "execute_if_required", execute)


async def revise_hypothesis(runtime: AppRuntime, project: str, spec: SandboxRunSpec) -> None:
    action = value(
        await runtime.bus.dispatch(
            request(
                "action/read", "p2-action", {"project_id": project, "action_id": spec.action_id}
            )
        )
    )["action"]
    hypothesis_id = action["hypothesis_refs"][0]
    hypothesis = value(
        await runtime.bus.dispatch(
            request(
                "hypothesis/read",
                "p2-hypothesis",
                {"project_id": project, "hypothesis_id": hypothesis_id},
            )
        )
    )["hypothesis"]
    value(
        await runtime.bus.dispatch(
            request(
                "hypothesis/revise",
                "p2-concurrent-edit",
                {
                    "project_id": project,
                    "hypothesis_id": hypothesis_id,
                    "expected_revision_digest": hypothesis["revision_digest"],
                    "patch": {"statement": "Operator revised the measurement hypothesis"},
                    "evidence_refs": hypothesis["evidence_refs"],
                    "reason": "new operator evidence",
                },
            )
        )
    )
