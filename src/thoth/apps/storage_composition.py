"""Select a registered storage provider before opening the runtime's stores."""

from dataclasses import dataclass
from pathlib import Path
from secrets import token_bytes

from thoth.adapters.storage.registry import default_store_factory_registry
from thoth.application.services import (
    BaselineRouter,
    BaselineService,
    FieldMeasurementService,
    ReceiptDagService,
)
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.store_bundle import StoreBundlePort, StoreFactoryPort, StoreFactoryRegistryPort


def open_stores(workspace: Path, factory: StoreFactoryPort | None = None) -> StoreBundlePort:
    selected = factory or default_store_factory_registry().resolve("sqlite", "1.0.0")
    return selected.open(workspace)


def open_registered_stores(
    workspace: Path, registry: StoreFactoryRegistryPort, provider_id: str, version: str
) -> StoreBundlePort:
    return registry.resolve(provider_id, version).open(workspace)


@dataclass(frozen=True)
class SharedStorageServices:
    baselines: BaselineService
    receipts: ReceiptDagService
    measurement: FieldMeasurementService


def create_shared_storage_services(
    stores: StoreBundlePort,
    clock: ClockPort,
    ids: IdGeneratorPort,
    measurement_secret: bytes | None,
) -> SharedStorageServices:
    return SharedStorageServices(
        baselines=BaselineService(
            store=stores.baselines,
            ledger=stores.ledger,
            router=BaselineRouter(),
            clock=clock,
            ids=ids,
        ),
        receipts=ReceiptDagService(
            store=stores.receipt_dag, ledger=stores.ledger, controls=stores.controls, clock=clock
        ),
        measurement=FieldMeasurementService(
            store=stores.field_measurement,
            clock=clock,
            ids=ids,
            pseudonym_secret=measurement_secret or token_bytes(32),
        ),
    )
