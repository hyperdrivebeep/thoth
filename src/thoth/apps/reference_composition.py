"""Compose reference behavior through existing Criterion/Source/Thread ports."""

from thoth.adapters.reference_calculators.registry import default_reference_calculators
from thoth.application.commands.reference import ReferenceThreadEntry
from thoth.application.services.criterion_contract_service import CriterionContractService
from thoth.application.services.reference_mapping import ModelReferenceMapper
from thoth.application.services.reference_service import ReferenceService
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.criterion_contract import CriterionContractStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.model import ModelResolverPort
from thoth.ports.project import ProjectStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.thread import ThreadEntryPort, ThreadStorePort


def reference_thread_entry(
    delegate: ThreadEntryPort,
    *,
    criteria: CriterionContractStorePort,
    service: CriterionContractService,
    artifacts: ArtifactLedgerPort,
    ledger: LedgerPort,
    threads: ThreadStorePort,
    clock: ClockPort,
    ids: IdGeneratorPort,
    models: ModelResolverPort,
    projects: ProjectStorePort,
) -> ReferenceThreadEntry:
    return ReferenceThreadEntry(
        delegate,
        ReferenceService(
            criteria=criteria,
            service=service,
            artifacts=artifacts,
            ledger=ledger,
            threads=threads,
            calculators=default_reference_calculators(),
            clock=clock,
            ids=ids,
        ),
        ModelReferenceMapper(models, projects, ledger),
    )
