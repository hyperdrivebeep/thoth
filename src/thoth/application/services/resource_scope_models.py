"""Revalidate all actual model input dependencies at each provider boundary."""

import json

from pydantic import BaseModel

from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.model_dispatch import UNVERIFIED_MODEL_CONTROL, ModelControlCapability
from thoth.domain.research_execution import research_work, reserve_model_dispatch
from thoth.domain.resource_scope import ResourceScopeError, current_resource_uses
from thoth.ports.behavior_execution import ModelCostBoundPort
from thoth.ports.model import ModelPort, ModelResolverPort
from thoth.ports.model_transport import ModelControlPort
from thoth.ports.resource_scope import ResourceAccessPort


class ResourceScopedModel:
    def __init__(self, model: ModelPort, access: ResourceAccessPort) -> None:
        self._model = model
        self._access = access

    @property
    def control_capability(self) -> ModelControlCapability:
        return (
            self._model.control_capability
            if isinstance(self._model, ModelControlPort)
            else UNVERIFIED_MODEL_CONTROL
        )

    def _require[T: BaseModel](self, request: ModelRequest[T]) -> None:
        context = request.context_pack
        project_id = request.project_id
        if context.project_id != project_id:
            raise ResourceScopeError("RESOURCE_ACCESS_DENIED")
        references: set[str] = set()
        # Includes source-bound memory excerpts appended by the normal Thread handler.
        for use in current_resource_uses() or ():
            if use.project_id == project_id and use.capability == "READ":
                references.add(use.resource_ref)
        for span in context.evidence:
            if span.project_id != project_id:
                raise ResourceScopeError("RESOURCE_ACCESS_DENIED")
            references.add(span.span_id)
        for criterion in context.criteria:
            for reference in criterion.evidence_refs:
                references.add(reference)
        records = (
            *context.canonical_hypotheses,
            context.canonical_portfolio,
            *context.canonical_actions,
            context.canonical_action_plan,
        )
        for record in records:
            if record is not None:
                if record.project_id != project_id:
                    raise ResourceScopeError("RESOURCE_ACCESS_DENIED")
                references.add(f"revision:{record.revision_digest}")
        for assessment in context.canonical_test_assessments:
            if assessment.project_id != project_id:
                raise ResourceScopeError("RESOURCE_ACCESS_DENIED")
            references.add(f"revision:{assessment.hypothesis_revision_digest}")
            references.add(f"revision:{assessment.plan_revision_digest}")
            for ref in assessment.observation_refs:
                references.add(ref)

        self._access.require_reads(project_id, tuple(sorted(references)))

    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        self._require(request)
        if research_work.get() is not None and not self.control_capability.owns_serialization:
            payload = json.dumps(
                {
                    "context": request.context_pack.model_dump(mode="json"),
                    "role": request.role.value,
                    "schema": request.output_model.model_json_schema(),
                    "prompt_version": request.prompt_version,
                    "model_policy_ref": request.model_policy_ref,
                    "max_output_tokens": request.max_output_tokens,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
            reserve_model_dispatch(payload, request.max_output_tokens, self.control_capability)
        result = await self._model.structured(request)
        self._require(request)
        return result

    def quote_max_cost_microunits[T: BaseModel](self, request: ModelRequest[T]) -> int | None:
        self._require(request)
        return (
            self._model.quote_max_cost_microunits(request)
            if isinstance(self._model, ModelCostBoundPort)
            else None
        )


class ResourceScopedModels:
    def __init__(self, models: ModelResolverPort, access: ResourceAccessPort) -> None:
        self._models = models
        self._access = access

    def resolve(self, *, provider: str, model: str | None) -> ModelPort:
        return ResourceScopedModel(
            self._models.resolve(provider=provider, model=model), self._access
        )
