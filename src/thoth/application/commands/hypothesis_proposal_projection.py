"""Pure proposal data and previews; applying and auditing remain outside these builders."""

from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.application.commands.hypothesis_inputs import MergeProposeInput, SplitProposeInput
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.hypothesis_full import HypothesisRecord


def split_proposal_data(request: SplitProposeInput) -> tuple[dict[str, JsonValue], str]:
    payload: dict[str, JsonValue] = {
        "project_id": request.project_id,
        "hypothesis_id": request.hypothesis_id,
        "subhypotheses": [dict(item) for item in request.subhypotheses],
        "evidence_refs": list(request.evidence_refs),
        "rationale": request.rationale,
        "expected_revision_digest": request.expected_revision_digest,
        "applied": False,
        "owner_namespace": "REVISION",
    }
    digest = domain_digest("HYPOTHESIS_SPLIT_PROPOSAL", "1.0.0", canonical_payload(payload))
    return payload, digest


def split_proposal_result(
    payload: dict[str, JsonValue], digest: str, current: HypothesisRecord
) -> dict[str, JsonValue]:
    return {
        "split_proposal": {**payload, "digest": digest},
        "lineage_preview": [f"SPECIALIZES:{current.hypothesis_id}"],
        "portfolio_impact_preview": [current.portfolio_id],
    }


def merge_proposal_data(
    request: MergeProposeInput, items: tuple[HypothesisRecord | None, ...]
) -> tuple[dict[str, JsonValue], str]:
    statements = {item.statement for item in items if item is not None}
    conflicts = [] if len(statements) == 1 else ["statement"]
    payload: dict[str, JsonValue] = {
        "project_id": request.project_id,
        "hypothesis_ids": list(request.hypothesis_ids),
        "field_mapping": request.field_mapping,
        "evidence_refs": list(request.evidence_refs),
        "rationale": request.rationale,
        "expected_revision_digests": list(request.expected_revision_digests),
        "semantic_conflicts": cast(JsonValue, conflicts),
        "applied": False,
        "owner_namespace": "REVISION",
    }
    digest = domain_digest("HYPOTHESIS_MERGE_PROPOSAL", "1.0.0", canonical_payload(payload))
    return payload, digest


def merge_proposal_result(
    request: MergeProposeInput, payload: dict[str, JsonValue], digest: str, first: HypothesisRecord
) -> dict[str, JsonValue]:
    return {
        "merge_proposal": {**payload, "digest": digest},
        "lineage_preview": [
            f"SUPERSEDES:{hypothesis_id}" for hypothesis_id in request.hypothesis_ids
        ],
        "portfolio_impact_preview": [first.portfolio_id],
    }
