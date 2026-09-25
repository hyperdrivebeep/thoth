from typing import Protocol

from thoth.domain.model_settings import ModelOption, ModelSelection


class ModelCatalogPort(Protocol):
    def options(self) -> tuple[ModelOption, ...]: ...
    def defaults(self) -> ModelSelection: ...
