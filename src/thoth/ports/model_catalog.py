from typing import Protocol, runtime_checkable

from thoth.domain.model_catalog import CatalogProviderStatus
from thoth.domain.model_settings import ModelOption, ModelSelection


class ModelCatalogPort(Protocol):
    def options(self) -> tuple[ModelOption, ...]: ...
    def defaults(self) -> ModelSelection: ...


@runtime_checkable
class CatalogStatusPort(Protocol):
    """Where each list came from and how fresh it is; read from what is already stored."""

    def statuses(self) -> tuple[CatalogProviderStatus, ...]: ...


@runtime_checkable
class ModelExecutionRecorderPort(Protocol):
    """Remember what a real request showed about one model. It never changes a choice."""

    def record_execution(
        self, provider: str, model: str, state: str, reason_code: str | None
    ) -> None: ...
