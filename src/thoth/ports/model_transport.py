from typing import Protocol, runtime_checkable

from thoth.domain.model_dispatch import (
    ModelControlCapability,
    ModelTransportReply,
    OAuthSession,
    PreparedModelDispatch,
)
from thoth.domain.model_settings import ResolvedModelSettings


class OAuthSessionPort(Protocol):
    def read(self) -> OAuthSession: ...


@runtime_checkable
class ModelControlPort(Protocol):
    @property
    def control_capability(self) -> ModelControlCapability: ...


@runtime_checkable
class BoundedModelExecutorPort(Protocol):
    @property
    def control_capability(self) -> ModelControlCapability: ...
    @property
    def model_label(self) -> str: ...
    def prepare(
        self,
        prompt: str,
        schema: dict[str, object],
        *,
        output_tokens: int,
        timeout_seconds: float | None,
        model_settings: ResolvedModelSettings | None = None,
    ) -> PreparedModelDispatch: ...
    async def dispatch(self, request: PreparedModelDispatch) -> ModelTransportReply: ...
