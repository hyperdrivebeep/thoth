"""A project's choice about retrying a model call that was cut off in the middle."""

from __future__ import annotations

from typing import Literal

from thoth.domain.base import DomainModel

# 사용자 결정 20261010-01: on for projects without a saved choice; saved False stays off.
# A retry can repeat remote work (no remote stop/idempotency proof). The existing limit remains
# at most twice, waiting about 2 s then 4 s; projects can turn it off and resume manually.
AUTO_RETRY_INTERRUPTED_DEFAULT = True
# 사용자 결정 2026-10-23, 가설 v3 실제 비교(12차 3/3, S2 3/3, 10-23 재비교).
# Only an absent choice defaults on; an explicitly saved False keeps the v2 contract.
HYPOTHESIS_CONTRACT_V3_DEFAULT = True


class ProjectModelCallSettings(DomainModel):
    record_kind: Literal["ProjectModelCallSettings"] = "ProjectModelCallSettings"
    schema_version: Literal["2.0.0"] = "2.0.0"
    project_id: str
    auto_retry_interrupted_model_call: bool = AUTO_RETRY_INTERRUPTED_DEFAULT
    hypothesis_contract_v3: bool = HYPOTHESIS_CONTRACT_V3_DEFAULT
