from __future__ import annotations

from collections.abc import Mapping
from typing import TypeVar, cast

from pydantic import BaseModel

from thoth.domain.canonical import canonical_payload, domain_digest, model_digest
from thoth.domain.model import ModelRequest, ModelResult
from thoth.ports.model import ModelPort

FixtureKey = tuple[str, str, str]
TModel = TypeVar("TModel", bound=BaseModel)


class ScriptedFixtureMissing(KeyError):
    pass


class ScriptedModel(ModelPort):
    model_id = "SCRIPTED_MODEL"

    def __init__(self, fixtures: Mapping[FixtureKey, Mapping[str, object]]) -> None:
        self._fixtures = dict(fixtures)

    def quote_max_cost_microunits(self, request: ModelRequest[TModel]) -> int:
        del request
        return 0

    async def structured(self, request: ModelRequest[TModel]) -> ModelResult[TModel]:
        key = (
            request.context_pack.case_id,
            request.role.value,
            request.prompt_version,
        )
        try:
            fixture = self._fixtures[key]
        except KeyError as exc:
            raise ScriptedFixtureMissing(key) from exc
        output = request.output_model.model_validate(fixture)
        input_digest = domain_digest(
            "MODEL_INPUT",
            "1.0.0",
            canonical_payload(
                {
                    "role": request.role,
                    "project_id": request.project_id,
                    "cutoff_at": request.cutoff_at,
                    "context_pack": request.context_pack,
                    "prompt_version": request.prompt_version,
                    "model_policy_ref": request.model_policy_ref,
                    "max_output_tokens": request.max_output_tokens,
                }
            ),
        )
        output_digest = model_digest(
            "MODEL_OUTPUT",
            cast(BaseModel, output),
            schema_version="1.0.0",
        )
        return ModelResult(
            output=output,
            model_id=self.model_id,
            prompt_version=request.prompt_version,
            scripted=True,
            input_digest=input_digest,
            output_digest=output_digest,
        )
