from __future__ import annotations

from dataclasses import dataclass

from thoth.adapters.evaluators.unconfigured import UnconfiguredImprovementEvaluator
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evaluator import EvaluatorCapability, EvaluatorIsolation
from thoth.domain.improvement import ImprovementEvaluation
from thoth.ports.improvement import ImprovementEvaluatorPort


class EvaluatorContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class EvaluatorRegistration:
    capability: EvaluatorCapability
    evaluator: ImprovementEvaluatorPort
    trusted_in_process: bool = False


class EvaluatorRegistry:
    def __init__(self, *, active_evaluator_id: str) -> None:
        self._active_evaluator_id = active_evaluator_id
        self._registrations: dict[str, EvaluatorRegistration] = {}

    def register(self, registration: EvaluatorRegistration) -> None:
        evaluator_id = registration.capability.evaluator_id
        if evaluator_id in self._registrations:
            raise EvaluatorContractError(f"evaluator already registered: {evaluator_id}")
        if (
            registration.capability.isolation == EvaluatorIsolation.IN_PROCESS_DETERMINISTIC
            and not registration.trusted_in_process
        ):
            raise EvaluatorContractError("in-process evaluator must be explicitly trusted")
        self._registrations[evaluator_id] = registration

    def capabilities(self) -> tuple[EvaluatorCapability, ...]:
        return tuple(self._registrations[key].capability for key in sorted(self._registrations))

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
        try:
            registration = self._registrations[self._active_evaluator_id]
        except KeyError as exc:
            raise EvaluatorContractError(
                f"active evaluator is not registered: {self._active_evaluator_id}"
            ) from exc
        capability = registration.capability
        if max_requests < 1 or max_requests > capability.max_requests:
            raise EvaluatorContractError("evaluator request budget exceeds capability")
        if timeout_seconds < 1 or timeout_seconds > capability.max_timeout_seconds:
            raise EvaluatorContractError("evaluator timeout exceeds capability")
        result = registration.evaluator.evaluate(
            baseline_digest=baseline_digest,
            candidate_digest=candidate_digest,
            fixture_digest=fixture_digest,
            hidden_holdout_digest=hidden_holdout_digest,
            max_requests=max_requests,
            timeout_seconds=timeout_seconds,
        )
        if result.evaluator_id != capability.evaluator_id:
            raise EvaluatorContractError("evaluator output identity mismatch")
        if result.candidate_digest != candidate_digest:
            raise EvaluatorContractError("evaluator output candidate mismatch")
        if result.fixture_digest != fixture_digest:
            raise EvaluatorContractError("evaluator output fixture mismatch")
        if result.hidden_holdout_digest_ref != hidden_holdout_digest:
            raise EvaluatorContractError("evaluator output holdout mismatch")
        return result


def default_evaluator_registry(
    override: ImprovementEvaluatorPort | None = None,
) -> EvaluatorRegistry:
    evaluator = override or UnconfiguredImprovementEvaluator()
    raw_id = getattr(evaluator, "evaluator_id", "OVERRIDE_EVALUATOR_V1")
    evaluator_id = raw_id if isinstance(raw_id, str) else "OVERRIDE_EVALUATOR_V1"
    input_schema_digest = domain_digest(
        "IMPROVEMENT_EVALUATOR_INPUT_SCHEMA",
        "1.0.0",
        canonical_payload(
            {
                "fields": [
                    "baseline_digest",
                    "candidate_digest",
                    "fixture_digest",
                    "hidden_holdout_digest",
                    "max_requests",
                    "timeout_seconds",
                ]
            }
        ),
    )
    output_schema_digest = domain_digest(
        "IMPROVEMENT_EVALUATOR_OUTPUT_SCHEMA",
        "1.0.0",
        canonical_payload({"model": "ImprovementEvaluation"}),
    )
    registry = EvaluatorRegistry(active_evaluator_id=evaluator_id)
    registry.register(
        EvaluatorRegistration(
            capability=EvaluatorCapability(
                evaluator_id=evaluator_id,
                version="1.0.0",
                input_schema_digest=input_schema_digest,
                output_schema_digest=output_schema_digest,
                isolation=EvaluatorIsolation.IN_PROCESS_DETERMINISTIC,
                max_requests=8,
                max_timeout_seconds=60,
                result_authority="TEST_ONLY" if override is not None else "UNCONFIGURED",
            ),
            evaluator=evaluator,
            trusted_in_process=True,
        )
    )
    return registry
