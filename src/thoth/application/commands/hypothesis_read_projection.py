"""Pure Hypothesis response projections; reads and mutations remain in the handler."""

from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.application.commands.hypothesis_inputs import (
    HypothesisListInput,
    PredictionListInput,
    RelationListInput,
)
from thoth.application.services.revision_service import CommitResult
from thoth.domain.hypothesis_full import (
    HypothesisRecord,
    HypothesisRelationRecord,
    PredictionRecord,
)


def hypothesis_summary(item: HypothesisRecord) -> dict[str, JsonValue]:
    return {
        "hypothesis_id": item.hypothesis_id,
        "object_id": item.object_id,
        "portfolio_id": item.portfolio_id,
        "statement": item.statement,
        "primary_intent": item.primary_intent,
        "intent_profile_refs": list(item.intent_profile_refs),
        "development_stage": item.development_stage,
        "empirical_appraisal": item.empirical_appraisal,
        "freshness": item.freshness,
        "revision_digest": item.revision_digest,
    }


def hypothesis_revision_result(
    before: HypothesisRecord, after: HypothesisRecord, commit: CommitResult
) -> dict[str, JsonValue]:
    before_data = before.model_dump(mode="json")
    after_data = after.model_dump(mode="json")
    diff = [
        {"path": key, "before": before_data.get(key), "after": after_data.get(key)}
        for key in sorted(set(before_data) | set(after_data))
        if before_data.get(key) != after_data.get(key)
    ]
    return cast(
        dict[str, JsonValue],
        {
            "hypothesis": after_data,
            "semantic_diff": diff,
            "invalidated_predictions_tests_appraisals": list(after.invalidated_refs),
            "stage": after.development_stage,
            "freshness": after.freshness,
            "commit": commit.model_dump(mode="json"),
        },
    )


def hypothesis_matches(item: HypothesisRecord, request: HypothesisListInput) -> bool:
    return (
        (request.object_id is None or item.object_id == request.object_id)
        and (request.portfolio_id is None or item.portfolio_id == request.portfolio_id)
        and (request.primary_intent is None or item.primary_intent == request.primary_intent)
        and (request.stage is None or item.development_stage == request.stage)
        and (request.appraisal is None or item.empirical_appraisal == request.appraisal)
        and (request.freshness is None or item.freshness == request.freshness)
    )


def hypothesis_graph_matches(item: HypothesisRecord, request: HypothesisListInput) -> bool:
    return (request.object_id is None or item.object_id == request.object_id) and (
        request.portfolio_id is None or item.portfolio_id == request.portfolio_id
    )


def hypothesis_relation_matches(item: HypothesisRelationRecord, request: RelationListInput) -> bool:
    return (
        request.hypothesis_id is None
        or request.hypothesis_id in {item.source_hypothesis_id, item.target_hypothesis_id}
    ) and (request.relation_type is None or item.relation_type == request.relation_type)


def hypothesis_prediction_matches(item: PredictionRecord, request: PredictionListInput) -> bool:
    return (
        request.prespecification_state is None
        or item.prespecification_state == request.prespecification_state
    ) and (request.observation_bound is None or item.observation_bound == request.observation_bound)


def hypothesis_graph_result(
    nodes: tuple[HypothesisRecord, ...],
    edges: tuple[HypothesisRelationRecord, ...],
    summaries: list[dict[str, JsonValue]],
) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "nodes": summaries,
            "edges": [item.model_dump(mode="json") for item in edges],
            "alternative_families": sorted(
                {item.primary_intent for item in nodes if item.primary_intent} - {"EXPLORATORY"}
            ),
            "unknown_reserve": [
                item.hypothesis_id for item in nodes if item.primary_intent == "EXPLORATORY"
            ],
        },
    )


def hypothesis_quality_result(item: HypothesisRecord) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "gateway_results": item.gateway_results,
            "epistemic_profile": item.quality_profile,
            "evaluator_identity": "deterministic-gateway+candidate-quality-profile:1",
            "rationale": "axes remain non-compensatory and are never averaged",
            "source_refs": list(item.evidence_refs),
        },
    )
