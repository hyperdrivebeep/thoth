from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from tests.integration.paired_evaluation_helpers import PairHarness, pair_harness
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a02_autonomous_acquisition import (
    A02Connector,
    DynamicA02Model,
    StaticModelResolver,
    policy_payload,
)

from thoth.adapters.connectors import ConnectorRegistry
from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.model import ModelRequest, ModelResult
from thoth.ports.model import ModelExecutionHold
from thoth.protocol.jsonrpc import JsonRpcResponse


class PolicyTestModel(DynamicA02Model):
    def __init__(self, *, metered: bool = True) -> None:
        super().__init__()
        self.metered = metered
        self.fail = False
        self.guidance: list[str | None] = []

    def quote_max_cost_microunits[T: BaseModel](self, request: ModelRequest[T]) -> int | None:
        del request
        return 0 if self.metered else None

    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        guidance = request.context_pack.policy_hints.get("behavior_guidance")
        self.guidance.append(guidance if isinstance(guidance, str) else None)
        if self.fail:
            raise ModelExecutionHold("injected policy regression")
        return await super().structured(request)


@dataclass
class ExposureHarness:
    pair: PairHarness
    model: PolicyTestModel
    exposure: dict[str, Any]
    thread: str
    role: str
    connector: A02Connector

    async def call(self, method: str, key: str, payload: dict[str, object]) -> JsonRpcResponse:
        return await self.pair.runtime.bus.dispatch(
            request(method, key, {"project_id": self.pair.project, **payload})
        )

    async def arm(self) -> dict[str, Any]:
        revision = 1
        if self.exposure["spec"]["stage"] == "CANARY":
            approved = value(
                await self.call(
                    "improvement/exposure/decide",
                    "approve",
                    {
                        "exposure_id": self.exposure["spec"]["exposure_id"],
                        "expected_revision": 1,
                        "approved_digest": self.exposure["spec"]["spec_digest"],
                        "decision": "APPROVE",
                        "actor_ref": "human:policy-owner",
                        "role_assignment_ref": self.role,
                    },
                )
            )
            revision = approved["exposure"]["revision"]
        return value(
            await self.call(
                "improvement/exposure/start",
                "arm",
                {
                    "exposure_id": self.exposure["spec"]["exposure_id"],
                    "expected_revision": revision,
                },
            )
        )

    async def finish_trial(self) -> dict[str, Any]:
        identifier = self.exposure["spec"]["exposure_id"]
        current = value(
            await self.call(
                "improvement/exposure/runtime/read", "finish-read", {"exposure_id": identifier}
            )
        )["exposure"]
        return value(
            await self.call(
                "improvement/exposure/complete",
                "finish-trial",
                {
                    "exposure_id": identifier,
                    "expected_revision": current["revision"],
                },
            )
        )["exposure"]

    async def prepare_baseline(self) -> dict[str, Any]:
        proposal = self.pair.proposal
        return value(
            await self.call(
                "improvement/promotion/prepare",
                "baseline-preview",
                {
                    "improvement_revision_id": proposal["record_id"],
                    "assessment_refs": [self.pair.plan["record_id"]],
                    "candidate_digest": proposal["payload"]["candidate_digest"],
                    "baseline_digest": proposal["payload"]["baseline_digest"],
                    "target_scope": proposal["payload"]["scope_key"],
                    "policy_version": "fixture",
                },
            )
        )["promotion"]

    async def approve_baseline(
        self, promotion: dict[str, Any], key: str = "approve-baseline"
    ) -> JsonRpcResponse:
        return await self.call(
            "improvement/promotion/decide",
            key,
            {
                "promotion_candidate_id": promotion["record_id"],
                "decision": "APPROVE",
                "approved_digest": promotion["record_digest"],
                "actor_ref": "human:policy-owner",
                "role_assignment_ref": self.role,
            },
        )


@asynccontextmanager
async def exposure_harness(
    workspace: Path,
    *,
    stage: str = "CANARY",
    metered: bool = True,
    duration: int = 300,
    requests: int = 3,
) -> AsyncGenerator[ExposureHarness]:
    model, connector = PolicyTestModel(metered=metered), A02Connector()
    policies = (
        {"kind": "PROMPT_BUNDLE", "version": "2.0.0", "task_guidance": "baseline-guidance"},
        {"kind": "PROMPT_BUNDLE", "version": "2.0.0", "task_guidance": "candidate-guidance"},
    )
    cases: list[dict[str, object]] = [
        {
            "public": {"case_id": "prompt", "payload": {}},
            "expected_output": {"behavior_guidance": "candidate-guidance"},
            "axis": "quality",
        }
    ]
    async with pair_harness(
        workspace,
        {},
        {},
        cases,
        policy_pair=policies,
        component=BehaviorArtifactKind.PROMPT_BUNDLE,
        target_component="PROMPT",
        executor_id="BEHAVIOR_COMPONENT_SUBPROCESS_V1",
        runtime_options={
            "model_resolver": StaticModelResolver(model),
            "connector_registry": ConnectorRegistry((connector,)),
        },
    ) as h:
        project = value(
            await h.runtime.bus.dispatch(
                request("project/read", "project", {"project_id": h.project})
            )
        )
        value(
            await h.runtime.bus.dispatch(
                request(
                    "project/policy/update",
                    "policy",
                    {
                        "project_id": h.project,
                        "expected_revision": project["revision"],
                        "payload": policy_payload(allow_connector=True),
                    },
                )
            )
        )
        for name in ("initial.md", "catalog.md"):
            value(
                await h.runtime.bus.dispatch(
                    request(
                        "project/source/connect",
                        name,
                        {
                            "project_id": h.project,
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
        thread = "thread:actual-exposure"
        value(
            await h.runtime.bus.dispatch(
                request(
                    "thread/start",
                    "thread",
                    {
                        "project_id": h.project,
                        "thread_id": thread,
                        "problem": "Which dataset_version produced the reported result?",
                        "scope": {"workstream": "evaluation"},
                    },
                )
            )
        )
        project = value(
            await h.runtime.bus.dispatch(
                request("project/read", "project-before-role", {"project_id": h.project})
            )
        )
        role = value(
            await h.runtime.bus.dispatch(
                request(
                    "project/role/assign",
                    "role",
                    {
                        "project_id": h.project,
                        "expected_revision": project["revision"],
                        "actor_id": "human:policy-owner",
                        "role": "improvement-owner",
                        "scope": "PROJECT",
                        "authority_tags": ["CAP_READ", "CAP_WRITE"],
                    },
                )
            )
        )["role"]["role_assignment_id"]
        result = (await h.run())["pair"]["result"]
        value(
            await h.runtime.bus.dispatch(
                request(
                    "improvement/evaluation/assess",
                    "assess",
                    {
                        "project_id": h.project,
                        "evaluation_plan_id": h.plan["record_id"],
                        "expected_evaluation_revision": 1,
                        "result_refs": [result["pair_id"]],
                        "evaluator_result_refs": [result["result_digest"]],
                        "exposure_ledger_ref": result["exposure_id"],
                    },
                )
            )
        )
        exposure = value(
            await h.runtime.bus.dispatch(
                request(
                    "improvement/exposure/prepare",
                    "prepare",
                    {
                        "project_id": h.project,
                        "improvement_revision_id": h.proposal["record_id"],
                        "evaluation_plan_id": h.plan["record_id"],
                        "requested_exposure_state": stage,
                        "scope": {"environment": "LOCAL", "thread_id": thread},
                        "duration_budget": {
                            "duration_seconds": duration,
                            "max_requests": requests,
                            "max_model_calls": 24,
                            "max_billed_cost_microunits": 0,
                        },
                        "stop_rollback_contract": {
                            "rollback_target_digest": h.plan["payload"]["baseline_digest"]
                        },
                    },
                )
            )
        )["runtime_exposure"]
        yield ExposureHarness(h, model, exposure, thread, role, connector)
