"""An absent evaluator supplies no scores or exposure evidence."""

from thoth.domain.evaluator import ImprovementEvaluationUnavailable
from thoth.domain.improvement import ImprovementEvaluation


class UnconfiguredImprovementEvaluator:
    evaluator_id = "UNCONFIGURED_IMPROVEMENT_EVALUATOR"

    def evaluate(
        self,
        *,
        baseline_digest: str,
        candidate_digest: str,
        fixture_digest: str,
        hidden_holdout_digest: str,
        max_requests: int,
        timeout_seconds: int,
    ) -> ImprovementEvaluation:
        del baseline_digest, candidate_digest, fixture_digest, hidden_holdout_digest
        del max_requests, timeout_seconds
        raise ImprovementEvaluationUnavailable("EVALUATOR_NOT_CONFIGURED")
