"""Own behavior content, consumers and the normal Thread envelope composition."""

from pathlib import Path

from thoth.adapters.behavior.registry import default_behavior_components
from thoth.adapters.behavior_catalog import FilesystemBehaviorArtifactCatalog
from thoth.adapters.retrieval_glossary import GLOSSARY_RELATIVE_PATH, GlossaryFileReader
from thoth.application.commands.behavior_execution import BehaviorThreadEntry
from thoth.application.services.behavior_candidate_service import BehaviorCandidateService
from thoth.application.services.behavior_exposure_runtime import BehaviorExposureRuntime
from thoth.application.services.behavior_models import BehaviorBoundModels
from thoth.application.services.behavior_snapshot_service import BehaviorSnapshotService
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.improvement_exposure_service import ImprovementExposureService
from thoth.application.services.improvement_runner import ImprovementRunner
from thoth.ports.behavior_artifact import BehaviorArtifactStorePort
from thoth.ports.behavior_resolver import BehaviorComponentRegistryPort
from thoth.ports.ledger import ManagedLedgerPort
from thoth.ports.model import ModelResolverPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.store_bundle import StoreBundlePort
from thoth.ports.thread import ThreadEntryPort
from thoth.protocol.registry import MethodRegistry


class BehaviorRuntime:
    def __init__(
        self,
        ledger: ManagedLedgerPort,
        stores: StoreBundlePort,
        clock: ClockPort,
        ids: IdGeneratorPort,
        components: BehaviorComponentRegistryPort,
        glossary_path: Path | None = None,
    ) -> None:
        self._glossary_reader = None if glossary_path is None else GlossaryFileReader(glossary_path)
        self._stores = stores
        self.store = stores.behaviors
        self.candidates = BehaviorCandidateService(
            store=self.store,
            clock=clock,
            ids=ids,
            catalog=FilesystemBehaviorArtifactCatalog(
                Path(__file__).resolve().parents[3] / "config"
            ),
        )
        self.runner = ImprovementRunner(store=self.store, clock=clock)
        self.snapshots = BehaviorSnapshotService(self.store, components)
        self._controls = ControlRecordService(store=stores.controls, clock=clock, ids=ids)
        self._ledger, self._clock, self._ids = ledger, clock, ids

    def wrap_models(self, models: ModelResolverPort) -> ModelResolverPort:
        return BehaviorBoundModels(models)

    def legacy(
        self,
    ) -> tuple[BehaviorArtifactStorePort, BehaviorCandidateService, ImprovementRunner]:
        return self.store, self.candidates, self.runner

    def connect_exposures(self, service: ImprovementExposureService) -> None:
        self.snapshots.control = BehaviorExposureRuntime(
            service=service,
            store=self._stores.behavior_executions,
            behaviors=self.store,
            components=default_behavior_components(),
            threads=self._stores.threads,
            ledger=self._ledger,
            clock=self._clock,
        )

    def register_thread(self, registry: MethodRegistry, delegate: ThreadEntryPort) -> None:
        entry = BehaviorThreadEntry(
            delegate,
            self.snapshots,
            self._controls,
            self._ledger,
            self._clock,
            self._ids,
            self._stores.projects,
            self._stores.threads,
            self._stores.governance,
            self._glossary_reader,
        )
        registry.register("thread/input", entry.analyze)


def create_behavior_runtime(
    ledger: ManagedLedgerPort,
    stores: StoreBundlePort,
    clock: ClockPort,
    ids: IdGeneratorPort,
    workspace: Path,
) -> BehaviorRuntime:
    return BehaviorRuntime(
        ledger,
        stores,
        clock,
        ids,
        default_behavior_components(),
        workspace / GLOSSARY_RELATIVE_PATH,
    )
