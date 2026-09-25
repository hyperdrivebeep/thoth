from __future__ import annotations

from typing import Protocol

from thoth.domain.criterion_contract import (
    CriterionProfileDecision,
    CriterionProfileRecord,
)
from thoth.domain.evidence import EvidenceSpan


class CriterionProfileRouterPort(Protocol):
    def route(
        self,
        *,
        project_id: str,
        evidence: tuple[EvidenceSpan, ...],
    ) -> CriterionProfileDecision: ...


class CriterionProfileCatalogPort(Protocol):
    def profiles(self) -> tuple[CriterionProfileRecord, ...]: ...


class CriterionProfileCandidateMapperPort(Protocol):
    def propose_profile_refs(
        self,
        *,
        project_id: str,
        evidence: tuple[EvidenceSpan, ...],
        available_profile_refs: tuple[str, ...],
    ) -> tuple[str, ...]: ...
