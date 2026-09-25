"""xAI Responses model. Shares structured-output repair, not Codex retry or quota."""

from __future__ import annotations

from typing import TypeVar, cast

from pydantic import BaseModel, ValidationError

from thoth.adapters.models.codex_oauth import (
    CodexStructuredOutputHold,
    constrain_action_families,
    prompt_envelope,
    strict_output_schema,
)
from thoth.adapters.models.reference_schema import constrain_span_references
from thoth.domain.canonical import canonical_payload, domain_digest, model_digest
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.model_dispatch import ModelControlCapability, PreparedModelDispatch
from thoth.domain.research_execution import (
    check_research_boundary,
    research_work,
    reserve_model_dispatch,
)
from thoth.ports.model import ModelPort, ModelTransportCancelled, ModelTransportHold
from thoth.ports.model_transport import BoundedModelExecutorPort

TModel = TypeVar("TModel", bound=BaseModel)


class XaiOAuthModel(ModelPort):
    def __init__(
        self,
        executor: BoundedModelExecutorPort,
        *,
        max_repair_attempts: int = 2,
    ) -> None:
        if max_repair_attempts < 0 or max_repair_attempts > 2:
            raise ValueError("max_repair_attempts must be between zero and two")
        self._executor = executor
        self._max_repair_attempts = max_repair_attempts

    @property
    def control_capability(self) -> ModelControlCapability:
        return self._executor.control_capability.model_copy(update={"owns_serialization": True})

    async def structured(self, request: ModelRequest[TModel]) -> ModelResult[TModel]:
        dispatches: list[str] = []
        prompt = _prompt_envelope(request)
        parsed: TModel | None = None
        validation_summary = "no structured output"
        final_prompt = prompt
        schema: dict[str, object] = {}
        for attempt in range(self._max_repair_attempts + 1):
            schema = constrain_span_references(
                strict_output_schema(request.output_model),
                tuple(span.span_id for span in request.context_pack.evidence),
                request.context_pack.research_context,
            )
            allowed_families = request.context_pack.policy_hints.get(
                "minimum_action_tier_by_family"
            )
            if isinstance(allowed_families, dict):
                family_mapping = cast(dict[object, object], allowed_families)
                schema = constrain_action_families(
                    schema, tuple(str(key) for key in family_mapping)
                )
            from thoth.adapters.models.reference_schema import apply_hypothesis_review_contract

            schema = apply_hypothesis_review_contract(
                schema,
                request.output_model,
                request.context_pack.research_context,
            )
            final_prompt = (
                prompt
                if attempt == 0
                else prompt
                + "\n\nREPAIR_TASK\nReturn exactly one JSON object matching the supplied schema. "
                + "Correct these validation errors from the prior response: "
                + validation_summary
            )
            work = research_work.get()
            prepared = self._executor.prepare(
                final_prompt,
                schema,
                output_tokens=request.max_output_tokens,
                timeout_seconds=300 if work is None else work.boundary.call_timeout(),
                model_settings=request.model_settings,
            )
            raw = await self._dispatch_once(prepared, dispatches)
            try:
                parsed = request.output_model.model_validate_json(raw)
            except ValidationError as exc:
                validation_summary = _xai_validation_summary(exc)
                continue
            break
        if parsed is None:
            raise CodexStructuredOutputHold("STRUCTURED_OUTPUT_UNAVAILABLE")
        return ModelResult(
            output=parsed,
            model_id=self._executor.model_label,
            prompt_version=request.prompt_version,
            scripted=False,
            dispatch_ids=tuple(dispatches),
            input_digest=domain_digest(
                "MODEL_PROMPT_INPUT",
                "2.0.0",
                canonical_payload(
                    {
                        "provider": "xai",
                        "prompt": final_prompt,
                        "schema": schema,
                        "prompt_version": request.prompt_version,
                        "model_policy_ref": request.model_policy_ref,
                        "max_output_tokens": request.max_output_tokens,
                        "model_settings": request.model_settings,
                    }
                ),
            ),
            output_digest=model_digest(
                "MODEL_OUTPUT",
                cast(BaseModel, parsed),
                schema_version="1.0.0",
            ),
        )

    async def _dispatch_once(
        self, prepared: PreparedModelDispatch, dispatches: list[str]
    ) -> str:
        work = research_work.get()
        check_research_boundary()
        dispatch_id = reserve_model_dispatch(
            prepared.payload, prepared.output_tokens_reserved, prepared.capability
        )
        dispatches.append(dispatch_id)
        try:
            reply = await self._executor.dispatch(prepared)
        except (ModelTransportHold, ModelTransportCancelled) as exc:
            if work is not None:
                work.boundary.record_usage(
                    dispatch_id,
                    exc.observation.received_bytes,
                    None,
                    None,
                    "UNKNOWN",
                    exc.observation.response_id,
                    observation=exc.observation,
                )
            if isinstance(exc, ModelTransportCancelled):
                import asyncio

                raise asyncio.CancelledError() from exc
            raise
        if work is not None:
            work.boundary.record_usage(
                dispatch_id,
                reply.received_bytes,
                reply.input_tokens,
                reply.output_tokens,
                reply.remote_stop,
                reply.response_id,
                observation=reply.observation,
                cached_input_tokens=reply.cached_input_tokens,
            )
        return reply.text


def _prompt_envelope(request: ModelRequest[BaseModel]) -> str:
    return prompt_envelope(request)


def _xai_validation_summary(error: ValidationError) -> str:
    items: list[str] = []
    for detail in error.errors(include_url=False, include_input=False)[:8]:
        location = ".".join(str(part) for part in detail.get("loc", ())) or "root"
        items.append(f"{location}:{detail.get('type')}:{detail.get('msg')}")
    return " | ".join(items)[:1500]
