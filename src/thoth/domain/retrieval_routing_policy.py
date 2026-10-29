"""Fixed experimental route, distinct from the immutable default retrieval policy."""

from typing import Any, Literal, cast

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.retrieval_embedding import DenseModelPin


class FusionMethodSpec(DomainModel):
    method_id: Literal["ROUND_ROBIN_DEDUP", "RRF"] = "ROUND_ROBIN_DEDUP"
    method_version: Literal["1.0.0"] = "1.0.0"
    k: Literal[60] | None = None

    @model_validator(mode="after")
    def _method_parameters(self) -> "FusionMethodSpec":
        if (self.method_id == "RRF") != (self.k == 60):
            raise ValueError("RETRIEVAL_FUSION_PARAMETERS_INVALID")
        return self

    @model_serializer(mode="wrap")
    def _legacy_bytes(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = cast(dict[str, Any], handler(self))
        if self.k is None:
            data.pop("k", None)
        return data


class RetrievalRoutingPolicy(DomainModel):
    kind: Literal["RETRIEVAL_POLICY"] = "RETRIEVAL_POLICY"
    version: Literal["5.0.0"] = "5.0.0"
    max_spans: int = Field(default=160, ge=1, le=160)
    character_budget: int = Field(default=50000, ge=1, le=50000)
    exposure: Literal["SHADOW_ONLY"] = "SHADOW_ONLY"
    routing: Literal["ELIGIBLE_ARTIFACT_COUNT"] = "ELIGIBLE_ARTIFACT_COUNT"
    model: DenseModelPin = DenseModelPin()
    fusion: FusionMethodSpec = FusionMethodSpec(method_id="RRF", k=60)

    @model_validator(mode="after")
    def _fixed_fusion(self) -> "RetrievalRoutingPolicy":
        if self.fusion.method_id != "RRF":
            raise ValueError("RETRIEVAL_ROUTING_REQUIRES_RRF")
        return self
