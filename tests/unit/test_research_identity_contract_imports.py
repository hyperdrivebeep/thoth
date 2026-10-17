"""Compatibility names resolve to the contract definitions actually used by the service."""

from typing import get_type_hints

from thoth.application.services import research_identity_contracts as contracts
from thoth.application.services import research_identity_service as service


def test_public_aliases_and_internal_return_type_share_contract_classes() -> None:
    assert service.ResearchContext is contracts.ResearchContext
    assert service.HypothesisOwnershipBatch is contracts.HypothesisOwnershipBatch
    assert service.ResearchOwnershipBatch is contracts.ResearchOwnershipBatch
    assert (
        get_type_hints(service.ResearchIdentityService.stage_actions)["return"]
        is contracts.ActionOwnershipBatch
    )
    assert (
        get_type_hints(service.ResearchOwnershipBatch)["actions"] is contracts.ActionOwnershipBatch
    )
