"""A project's choice about retrying a model call that was cut off in the middle."""

from __future__ import annotations

from typing import Literal

from thoth.domain.base import DomainModel

# The one place the default lives. Off: a cut-off call is shown to the user, who decides (the
# result card offers "이어서 조사"). The server cannot tell whether it already received a request
# (no idempotency) or whether the cut-off one stopped, so a send-again can repeat the same work
# or a runaway; a project turns it on knowingly. When on: at most twice, 2 s then 4 s.
AUTO_RETRY_INTERRUPTED_DEFAULT = False
# Hypothesis generation contract v3 (what each hypothesis expects from a test, and what would refute
# it): off, so a project keeps the v2 contract exactly until it turns this on.
HYPOTHESIS_CONTRACT_V3_DEFAULT = False


class ProjectModelCallSettings(DomainModel):
    record_kind: Literal["ProjectModelCallSettings"] = "ProjectModelCallSettings"
    schema_version: Literal["2.0.0"] = "2.0.0"
    project_id: str
    auto_retry_interrupted_model_call: bool = AUTO_RETRY_INTERRUPTED_DEFAULT
    hypothesis_contract_v3: bool = HYPOTHESIS_CONTRACT_V3_DEFAULT
