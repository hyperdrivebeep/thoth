from __future__ import annotations

from asyncio import CancelledError
from typing import Protocol, TypeVar

from pydantic import BaseModel

from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.model_dispatch import ModelReceiveObservation

TModel = TypeVar("TModel", bound=BaseModel)


class ModelPort(Protocol):
    async def structured(self, request: ModelRequest[TModel]) -> ModelResult[TModel]: ...


class ModelExecutionHold(RuntimeError):
    pass


class ModelOutputContractHold(ModelExecutionHold):
    pass


class ModelTransportHold(ModelExecutionHold):
    def __init__(self, reason: str, observation: ModelReceiveObservation) -> None:
        super().__init__(reason)
        self.observation = observation
        self.http_rejection = observation.http_rejection


class ModelTransportCancelled(CancelledError):
    """Preserve received metadata while retaining cancellation, not a retryable failure."""

    def __init__(self, observation: ModelReceiveObservation) -> None:
        super().__init__("MODEL_TRANSPORT_CANCELLED_REMOTE_STOP_UNKNOWN")
        self.observation = observation


class ModelResolutionError(ValueError):
    pass


class ModelResolverPort(Protocol):
    def resolve(self, *, provider: str, model: str | None) -> ModelPort: ...
