from __future__ import annotations

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.improvement import ImprovementEvaluation
from thoth.ports.improvement import ImprovementEvaluatorPort


class DeterministicIndependentImprovementEvaluator(ImprovementEvaluatorPort):
    """Explicit TEST_ONLY fixture; fixed outcomes are never measured product evidence."""

    evaluator_id = "DETERMINISTIC_INDEPENDENT_EVALUATOR_V1"
    result_authority = "TEST_ONLY"

    def __init__(self, outcome: dict[str, object] | None = None) -> None:
        self._outcome = outcome or {}

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
        draft: dict[str, object] = {
            "evaluator_id": self.evaluator_id,
            "fixture_digest": fixture_digest,
            "candidate_digest": candidate_digest,
            "baseline_quality_bps": self._outcome.get("baseline_quality_bps", 5_000),
            "candidate_quality_bps": self._outcome.get("candidate_quality_bps", 7_500),
            "baseline_safety_bps": self._outcome.get("baseline_safety_bps", 10_000),
            "candidate_safety_bps": self._outcome.get("candidate_safety_bps", 10_000),
            "baseline_cost_microunits": self._outcome.get("baseline_cost_microunits", 1_000_000),
            "candidate_cost_microunits": self._outcome.get("candidate_cost_microunits", 900_000),
            "critical_regression": self._outcome.get("critical_regression", False),
            "hidden_holdout_exposed": self._outcome.get("hidden_holdout_exposed", False),
            "budget_exhausted": self._outcome.get("budget_exhausted", max_requests < 1),
            "timed_out": self._outcome.get("timed_out", timeout_seconds < 1),
            "hidden_holdout_digest_ref": hidden_holdout_digest,
            "baseline_digest": baseline_digest,
        }
        return ImprovementEvaluation.model_validate(
            {
                **draft,
                "evaluation_digest": domain_digest(
                    "IMPROVEMENT_EVALUATION", "1.0.0", canonical_payload(draft)
                ),
            }
        )
