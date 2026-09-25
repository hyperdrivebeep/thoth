"""Criterion service composition; store selection remains outside this module."""

from pathlib import Path

from thoth.adapters.criterion_profiles import FilesystemCriterionProfileCatalog
from thoth.application.services.criterion_contract_service import CriterionContractService
from thoth.application.services.revision_service import RevisionCommitService
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.criterion_contract import CriterionContractStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


def create_criterion_service(
    store: CriterionContractStorePort,
    artifacts: ArtifactLedgerPort,
    ledger: LedgerPort,
    clock: ClockPort,
    ids: IdGeneratorPort,
) -> CriterionContractService:
    result = CriterionContractService(
        store=store,
        artifacts=artifacts,
        ledger=ledger,
        commits=RevisionCommitService(
            ledger, clock, ids, policy_version="criterion-contract:1.0.0"
        ),
        clock=clock,
        ids=ids,
        profile_catalog=FilesystemCriterionProfileCatalog(
            Path(__file__).resolve().parents[3] / "config" / "criterion-profiles"
        ),
    )
    result.seed_profiles()
    return result
