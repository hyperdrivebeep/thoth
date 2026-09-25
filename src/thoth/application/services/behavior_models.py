"""Use captured prompt policy; shadow rendering never changes the returned model result."""

import hashlib
import json
from dataclasses import replace
from time import perf_counter_ns

from pydantic import BaseModel

from thoth.application.services.behavior_context import current_behavior_work
from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.behavior_execution import ActiveBehaviorSnapshot, BehaviorUse
from thoth.domain.behavior_policy import (
    BehaviorPolicyError,
    PromptBehaviorPolicy,
    render_prompt_policy,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.model_dispatch import UNVERIFIED_MODEL_CONTROL, ModelControlCapability
from thoth.ports.behavior_execution import ModelCostBoundPort
from thoth.ports.model import ModelPort, ModelResolverPort
from thoth.ports.model_transport import ModelControlPort


def _request_digest[T: BaseModel](request: ModelRequest[T]) -> str:
    return domain_digest(
        "BEHAVIOR_MODEL_INPUT",
        "1.0.0",
        canonical_payload(
            {
                "role": request.role,
                "project_id": request.project_id,
                "cutoff_at": request.cutoff_at,
                "context": request.context_pack,
                "prompt_version": request.prompt_version,
                "model_policy_ref": request.model_policy_ref,
                "max_output_tokens": request.max_output_tokens,
                "output_schema_digest": hashlib.sha256(
                    json.dumps(
                        request.output_model.model_json_schema(),
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
            }
        ),
    )


def _prompt_request[T: BaseModel](
    request: ModelRequest[T], snapshot: ActiveBehaviorSnapshot
) -> ModelRequest[T]:
    policy = snapshot.policy
    if not isinstance(policy, PromptBehaviorPolicy):
        raise BehaviorPolicyError("BEHAVIOR_POLICY_KIND_MISMATCH")
    if not policy.task_guidance:
        return request
    return replace(
        request,
        context_pack=request.context_pack.model_copy(
            update={
                "policy_hints": {
                    **request.context_pack.policy_hints,
                    **render_prompt_policy(policy),
                    "behavior_snapshot_digest": snapshot.snapshot_digest,
                },
            }
        ),
    )


class BehaviorBoundModel:
    def __init__(self, delegate: ModelPort) -> None:
        self._delegate = delegate

    @property
    def control_capability(self) -> ModelControlCapability:
        return (
            self._delegate.control_capability
            if isinstance(self._delegate, ModelControlPort)
            else UNVERIFIED_MODEL_CONTROL
        )

    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        context = current_behavior_work()
        if context is None:
            return await self._delegate.structured(request)
        if context.project_id != request.project_id:
            raise BehaviorPolicyError("BEHAVIOR_WORK_PROJECT_MISMATCH")
        snapshot = context.snapshot(BehaviorArtifactKind.PROMPT_BUNDLE)
        original = request
        original_digest = _request_digest(original)
        started = perf_counter_ns()
        request = _prompt_request(original, snapshot)
        before = _request_digest(request)
        context.uses.append(
            BehaviorUse(
                component=snapshot.component,
                snapshot_digest=snapshot.snapshot_digest,
                operation="PROMPT_RENDER",
                input_digest=original_digest,
                output_digest=before,
                elapsed_ns=perf_counter_ns() - started,
                billed_cost_microunits=0,
                cost_basis="NOT_BILLABLE",
            )
        )
        for shadow in context.shadows:
            if shadow.component != BehaviorArtifactKind.PROMPT_BUNDLE:
                continue
            started = perf_counter_ns()
            rendered = _prompt_request(original, shadow)
            context.uses.append(
                BehaviorUse(
                    component=shadow.component,
                    snapshot_digest=shadow.snapshot_digest,
                    operation="PROMPT_RENDER",
                    input_digest=original_digest,
                    output_digest=_request_digest(rendered),
                    elapsed_ns=perf_counter_ns() - started,
                    billed_cost_microunits=0,
                    cost_basis="NOT_BILLABLE",
                )
            )
        if context.control is not None and any(
            item.origin == "CANARY" for item in context.snapshots
        ):
            quote = (
                self._delegate.quote_max_cost_microunits(request)
                if isinstance(self._delegate, ModelCostBoundPort)
                else None
            )
            context.control.before_model(context.snapshots, quote)
        context.uses.append(
            BehaviorUse(
                component=snapshot.component,
                snapshot_digest=snapshot.snapshot_digest,
                operation="MODEL_DISPATCH",
                input_digest=before,
                output_digest=before,
                elapsed_ns=0,
                billed_cost_microunits=None,
                cost_basis="UNKNOWN",
            )
        )
        started = perf_counter_ns()
        result = await self._delegate.structured(request)
        if context.control is not None:
            context.control.validate_work(context.snapshots)
        context.uses.append(
            BehaviorUse(
                component=snapshot.component,
                snapshot_digest=snapshot.snapshot_digest,
                operation="MODEL_INPUT",
                input_digest=before,
                output_digest=domain_digest(
                    "BEHAVIOR_MODEL_OUTPUT", "1.0.0", canonical_payload(result.output)
                ),
                elapsed_ns=perf_counter_ns() - started,
                billed_cost_microunits=0 if result.scripted else None,
                cost_basis="LOCAL_SCRIPTED" if result.scripted else "UNKNOWN",
            )
        )
        return result


class BehaviorBoundModels:
    def __init__(self, delegate: ModelResolverPort) -> None:
        self._delegate = delegate

    def resolve(self, *, provider: str, model: str | None) -> ModelPort:
        return BehaviorBoundModel(self._delegate.resolve(provider=provider, model=model))
