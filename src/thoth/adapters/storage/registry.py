"""Versioned store factory registration; unknown or duplicate registrations fail explicitly."""

from collections.abc import Iterable

from thoth.adapters.storage.bundle import SqliteStoreFactory
from thoth.ports.store_bundle import StoreFactoryPort


class StoreFactoryRegistry:
    def __init__(self, factories: Iterable[StoreFactoryPort] = ()) -> None:
        self._factories: dict[tuple[str, str], StoreFactoryPort] = {}
        for factory in factories:
            self.register(factory)

    def register(self, factory: StoreFactoryPort) -> None:
        key = (factory.provider_id, factory.version)
        if not all(key):
            raise ValueError("STORE_FACTORY_IDENTITY_REQUIRED")
        if key in self._factories:
            raise ValueError("STORE_FACTORY_ALREADY_REGISTERED")
        self._factories[key] = factory

    def resolve(self, provider_id: str, version: str) -> StoreFactoryPort:
        try:
            return self._factories[(provider_id, version)]
        except KeyError:
            raise ValueError("STORE_FACTORY_NOT_REGISTERED") from None


def default_store_factory_registry() -> StoreFactoryRegistry:
    return StoreFactoryRegistry((SqliteStoreFactory(),))
