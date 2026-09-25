from __future__ import annotations

from collections.abc import Callable

from thoth.ports.model import ModelPort, ModelResolutionError, ModelResolverPort

ModelFactory = Callable[[str | None], ModelPort]
ModelAvailability = Callable[[str], bool]
DynamicModelFactory = Callable[[str, str | None], ModelPort]


class RegisteredModelResolver(ModelResolverPort):
    def __init__(self) -> None:
        self._factories: dict[str, ModelFactory] = {}
        self._dynamic: list[tuple[ModelAvailability, DynamicModelFactory]] = []

    def register(self, provider: str, factory: ModelFactory) -> None:
        normalized = provider.strip().lower()
        if not normalized or normalized in self._factories:
            raise ValueError(f"model provider already registered or invalid: {provider}")
        self._factories[normalized] = factory

    def register_dynamic(
        self, available: ModelAvailability, factory: DynamicModelFactory
    ) -> None:
        self._dynamic.append((available, factory))

    def contains(self, provider: str) -> bool:
        normalized = provider.strip().lower()
        return normalized in self._factories or any(
            available(normalized) for available, _ in self._dynamic
        )

    def resolve(self, *, provider: str, model: str | None) -> ModelPort:
        normalized = provider.strip().lower()
        factory = self._factories.get(normalized)
        if factory is not None:
            return factory(model)
        for available, dynamic_factory in self._dynamic:
            if available(normalized):
                return dynamic_factory(normalized, model)
        raise ModelResolutionError(f"model provider is not registered: {provider}")
