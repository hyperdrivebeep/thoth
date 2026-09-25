from __future__ import annotations

import pytest

from thoth.adapters.evaluators import (
    EvaluatorContractError,
    EvaluatorRegistration,
    EvaluatorRegistry,
)
from thoth.adapters.improvement import DeterministicIndependentImprovementEvaluator
from thoth.domain.evaluator import EvaluatorCapability, EvaluatorIsolation


def capability(evaluator_id: str, *, max_requests: int = 8) -> EvaluatorCapability:
    return EvaluatorCapability(
        evaluator_id=evaluator_id,
        version="1.0.0",
        input_schema_digest="a" * 64,
        output_schema_digest="b" * 64,
        isolation=EvaluatorIsolation.IN_PROCESS_DETERMINISTIC,
        max_requests=max_requests,
        max_timeout_seconds=60,
    )


def test_new_evaluator_runs_after_registry_only_registration() -> None:
    evaluator = DeterministicIndependentImprovementEvaluator()
    registry = EvaluatorRegistry(active_evaluator_id=evaluator.evaluator_id)
    registry.register(
        EvaluatorRegistration(
            capability=capability(evaluator.evaluator_id),
            evaluator=evaluator,
            trusted_in_process=True,
        )
    )

    result = registry.evaluate(
        baseline_digest="1" * 64,
        candidate_digest="2" * 64,
        fixture_digest="3" * 64,
        hidden_holdout_digest="4" * 64,
        max_requests=8,
        timeout_seconds=60,
    )

    assert result.evaluator_id == evaluator.evaluator_id
    assert result.candidate_digest == "2" * 64


def test_registry_rejects_budget_before_evaluator_io() -> None:
    evaluator = DeterministicIndependentImprovementEvaluator()
    registry = EvaluatorRegistry(active_evaluator_id=evaluator.evaluator_id)
    registry.register(
        EvaluatorRegistration(
            capability=capability(evaluator.evaluator_id, max_requests=2),
            evaluator=evaluator,
            trusted_in_process=True,
        )
    )

    with pytest.raises(EvaluatorContractError, match="request budget"):
        registry.evaluate(
            baseline_digest="1" * 64,
            candidate_digest="2" * 64,
            fixture_digest="3" * 64,
            hidden_holdout_digest="4" * 64,
            max_requests=3,
            timeout_seconds=60,
        )


def test_registry_rejects_timeout_contract_before_evaluator_io() -> None:
    evaluator = DeterministicIndependentImprovementEvaluator()
    registry = EvaluatorRegistry(active_evaluator_id=evaluator.evaluator_id)
    registry.register(
        EvaluatorRegistration(
            capability=capability(evaluator.evaluator_id),
            evaluator=evaluator,
            trusted_in_process=True,
        )
    )

    with pytest.raises(EvaluatorContractError, match="timeout"):
        registry.evaluate(
            baseline_digest="1" * 64,
            candidate_digest="2" * 64,
            fixture_digest="3" * 64,
            hidden_holdout_digest="4" * 64,
            max_requests=1,
            timeout_seconds=61,
        )


def test_registry_rejects_untrusted_in_process_evaluator() -> None:
    evaluator = DeterministicIndependentImprovementEvaluator()
    registry = EvaluatorRegistry(active_evaluator_id=evaluator.evaluator_id)

    with pytest.raises(EvaluatorContractError, match="explicitly trusted"):
        registry.register(
            EvaluatorRegistration(
                capability=capability(evaluator.evaluator_id),
                evaluator=evaluator,
            )
        )


def test_registry_rejects_output_identity_mismatch() -> None:
    class MismatchedOutputEvaluator(DeterministicIndependentImprovementEvaluator):
        def evaluate(self, **kwargs: object):  # type: ignore[no-untyped-def]
            result = super().evaluate(**kwargs)  # type: ignore[arg-type]
            return result.model_copy(update={"evaluator_id": "OTHER_EVALUATOR"})

    evaluator = MismatchedOutputEvaluator()
    registry = EvaluatorRegistry(active_evaluator_id=evaluator.evaluator_id)
    registry.register(
        EvaluatorRegistration(
            capability=capability(evaluator.evaluator_id),
            evaluator=evaluator,
            trusted_in_process=True,
        )
    )

    with pytest.raises(EvaluatorContractError, match="identity mismatch"):
        registry.evaluate(
            baseline_digest="1" * 64,
            candidate_digest="2" * 64,
            fixture_digest="3" * 64,
            hidden_holdout_digest="4" * 64,
            max_requests=1,
            timeout_seconds=60,
        )
