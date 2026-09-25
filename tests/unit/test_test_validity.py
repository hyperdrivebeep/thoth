from datetime import UTC, datetime
from decimal import Decimal

import pytest

from thoth.adapters.evaluators.research_test import default_research_test_evaluator
from thoth.application.services.execution_preparation import execution_checkpoint
from thoth.domain.test_validity import (
    ResearchMeasurementContract,
    ResearchMeasurementObservation,
    hypothesis_semantic_digest,
)


def contract(version: str = "numeric-mean:1") -> ResearchMeasurementContract:
    return ResearchMeasurementContract(
        record_type="RESEARCH_MEASUREMENT_CONTRACT",
        contract_id="measurement:mean",
        method="ARITHMETIC_MEAN",
        procedure_version=version,
        measure="latency",
        unit="ms",
        conditions={"dataset": "fixture"},
        minimum_samples=2,
        maximum_samples=10,
        image_digest="image:fixture",
        runtime_version="fixture:1",
    )


@pytest.mark.parametrize(
    "samples,expected",
    [((Decimal(10), Decimal(12)), Decimal(11)), ((Decimal(2), Decimal(8)), Decimal(5))],
)
def test_registered_measurement_producer_computes_from_each_raw_sample_set(
    samples: tuple[Decimal, ...], expected: Decimal
) -> None:
    observation = ResearchMeasurementObservation(
        record_type="RESEARCH_MEASUREMENT_OBSERVATION",
        contract_digest="a" * 64,
        procedure_version="numeric-mean:1",
        measure="latency",
        unit="ms",
        conditions={"dataset": "fixture"},
        samples=samples,
        input_digests=("b" * 64,),
        observed_at=datetime(2026, 9, 6, tzinfo=UTC),
    )
    assert default_research_test_evaluator().evaluate(contract(), observation) == expected
    with pytest.raises(ValueError, match="MEASUREMENT_PRODUCER_NOT_REGISTERED"):
        default_research_test_evaluator().evaluate(contract("unregistered:2"), observation)


def test_execution_checkpoint_distinguishes_selected_step_scope() -> None:
    first = execution_checkpoint("execution:1", "a" * 64, 1, ("step:read",), {}, (), ("step:read",))
    wider = execution_checkpoint(
        "execution:1", "a" * 64, 1, ("step:read",), {}, (), ("step:read", "step:protected")
    )
    assert first != wider


def test_binding_and_appraisal_metadata_do_not_change_hypothesis_basis() -> None:
    basis: dict[str, object] = {
        "project_id": "project:1",
        "object_id": "object:1",
        "hypothesis_id": "hypothesis:1",
        "statement": "Mean is in range",
        "scope": {"dataset": "v1"},
    }
    original = hypothesis_semantic_digest(basis)
    assert (
        hypothesis_semantic_digest(
            {
                **basis,
                "prediction_refs": ("prediction:1",),
                "empirical_appraisal": "EVIDENCE_FAVORS",
            }
        )
        == original
    )
    assert (
        hypothesis_semantic_digest({**basis, "statement": "Mean is outside the range"}) != original
    )
    assert hypothesis_semantic_digest({**basis, "scope": {"dataset": "v2"}}) != original
