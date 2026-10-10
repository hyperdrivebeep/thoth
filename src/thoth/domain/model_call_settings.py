"""A project's choice about retrying a model call that was cut off in the middle."""

from __future__ import annotations

from typing import Literal

from thoth.domain.base import DomainModel

# 사용자 결정 20261010-01: on for projects without a saved choice; saved False stays off.
# A retry can repeat remote work (no remote stop/idempotency proof). The existing limit remains
# at most twice, waiting about 2 s then 4 s; projects can turn it off and resume manually.
AUTO_RETRY_INTERRUPTED_DEFAULT = True
# Hypothesis generation contract v3 (what each hypothesis expects from a test, and what would refute
# it): off, so a project keeps the v2 contract exactly until it turns this on.
HYPOTHESIS_CONTRACT_V3_DEFAULT = False


class ProjectModelCallSettings(DomainModel):
    record_kind: Literal["ProjectModelCallSettings"] = "ProjectModelCallSettings"
    schema_version: Literal["2.0.0"] = "2.0.0"
    project_id: str
    auto_retry_interrupted_model_call: bool = AUTO_RETRY_INTERRUPTED_DEFAULT
    hypothesis_contract_v3: bool = HYPOTHESIS_CONTRACT_V3_DEFAULT
