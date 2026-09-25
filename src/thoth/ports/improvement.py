from __future__ import annotations

from typing import Protocol

from thoth.domain.improvement import ImprovementEvaluation


class ImprovementEvaluatorPort(Protocol):
    def evaluate(
        self,
        *,
        baseline_digest: str,
        candidate_digest: str,
        fixture_digest: str,
        hidden_holdout_digest: str,
        max_requests: int,
        timeout_seconds: int,
    ) -> ImprovementEvaluation: ...
