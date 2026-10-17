"""Detailed Hypothesis queries; validation and authority guards remain in the handler."""

from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.application.commands.hypothesis_inputs import (
    AssumptionListInput,
    HypothesisReadInput,
    PortfolioListInput,
    PortfolioReadInput,
    PredictionReadInput,
)
from thoth.domain.hypothesis_full import (
    HypothesisAppraisalRecord,
    HypothesisPortfolioRecord,
    HypothesisRecord,
    PredictionRecord,
)
from thoth.ports.hypothesis import HypothesisStorePort


def hypothesis_detail(
    request: HypothesisReadInput, item: HypothesisRecord, store: HypothesisStorePort
) -> dict[str, JsonValue]:
    return {
        "hypothesis": item.model_dump(mode="json"),
        "assumptions": [
            value.model_dump(mode="json")
            for value in store.list_assumptions(request.project_id, request.hypothesis_id)
        ],
        "predictions": [
            value.model_dump(mode="json")
            for value in store.list_predictions(request.project_id, request.hypothesis_id)
        ],
        "appraisals": [
            value.model_dump(mode="json")
            for value in store.list_appraisals(request.project_id, request.hypothesis_id)
        ],
    }


def portfolio_list_result(
    request: PortfolioListInput, store: HypothesisStorePort
) -> dict[str, JsonValue]:
    items = tuple(
        item
        for item in store.list_portfolios(request.project_id)
        if request.object_id is None or item.object_id == request.object_id
    )
    return {"portfolios": [item.model_dump(mode="json") for item in items], "next_cursor": None}


def portfolio_detail(
    request: PortfolioReadInput, item: HypothesisPortfolioRecord, store: HypothesisStorePort
) -> dict[str, JsonValue]:
    return {
        "portfolio": item.model_dump(mode="json"),
        "relations": [
            relation.model_dump(mode="json")
            for relation in store.list_relations(request.project_id, request.portfolio_id)
        ],
    }


def prediction_detail(
    request: PredictionReadInput, item: PredictionRecord, store: HypothesisStorePort
) -> dict[str, JsonValue]:
    return {
        "prediction": item.model_dump(mode="json"),
        "test_bindings": [
            binding.model_dump(mode="json")
            for binding in store.list_test_bindings(request.project_id, request.prediction_id)
        ],
    }


def assumption_list_result(
    request: AssumptionListInput, store: HypothesisStorePort
) -> dict[str, JsonValue]:
    items = store.list_assumptions(request.project_id, request.hypothesis_id)
    if request.prediction_id is not None:
        prediction = store.read_prediction(request.prediction_id)
        allowed = set(() if prediction is None else prediction.assumption_refs)
        items = tuple(item for item in items if item.assumption_id in allowed)
    if request.status is not None:
        items = tuple(item for item in items if item.status == request.status)
    return {"assumptions": [item.model_dump(mode="json") for item in items]}


def appraisal_detail(
    request: HypothesisReadInput,
    item: HypothesisRecord,
    appraisals: tuple[HypothesisAppraisalRecord, ...],
    store: HypothesisStorePort,
) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "hypothesis_revision_digest": item.revision_digest,
            "cumulative_evidence": list(
                dict.fromkeys(ref for record in appraisals for ref in record.evidence_refs)
            ),
            "test_validity_chain": [
                binding.model_dump(mode="json")
                for binding in store.list_test_bindings(request.project_id, None)
                if binding.test_binding_id
                in {ref for record in appraisals for ref in record.test_assessment_refs}
            ],
            "appraisals": [record.model_dump(mode="json") for record in appraisals],
            "current_scoped_appraisal": item.empirical_appraisal,
        },
    )


def hypothesis_audit_result(
    request: HypothesisReadInput, store: HypothesisStorePort
) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "records": [
                record.model_dump(mode="json")
                for record in store.list_audit(request.project_id, request.hypothesis_id)
            ]
        },
    )
