"""Pure Object proposal data and previews; guards and audit writes remain in the handler."""

from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.application.commands.object_inputs import MergeProposeInput, SplitProposeInput
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.decision_object_full import DecisionObjectRecord


def object_split_proposal_data(request: SplitProposeInput) -> tuple[dict[str, JsonValue], str]:
    payload: dict[str, JsonValue] = {
        "project_id": request.project_id,
        "object_id": request.object_id,
        "partitions": [dict(item) for item in request.partitions],
        "evidence_refs": list(request.evidence_refs),
        "rationale": request.rationale,
        "expected_revision_digest": request.expected_revision_digest,
        "applied": False,
        "owner_namespace": "REVISION",
    }
    digest = domain_digest("OBJECT_SPLIT_PROPOSAL", "1.0.0", canonical_payload(payload))
    return payload, digest


def object_split_proposal_result(
    request: SplitProposeInput, payload: dict[str, JsonValue], digest: str
) -> dict[str, JsonValue]:
    return {
        "split_proposal": {**payload, "digest": digest},
        "lineage_preview": [f"PART_OF:{request.object_id}"],
        "impact_preview": ["dependent projections become stale after application"],
    }


def object_merge_proposal_data(
    request: MergeProposeInput, objects: tuple[DecisionObjectRecord | None, ...]
) -> tuple[dict[str, JsonValue], str]:
    conflicts = sorted(
        {
            "problem_frame"
            for item in objects[1:]
            if item is not None
            and objects[0] is not None
            and (item.problem_frame != objects[0].problem_frame)
        }
    )
    payload: dict[str, JsonValue] = {
        "project_id": request.project_id,
        "object_ids": list(request.object_ids),
        "field_mapping": request.field_mapping,
        "evidence_refs": list(request.evidence_refs),
        "rationale": request.rationale,
        "expected_revision_digests": list(request.expected_revision_digests),
        "applied": False,
        "owner_namespace": "REVISION",
        "conflicts": cast(JsonValue, conflicts),
    }
    digest = domain_digest("OBJECT_MERGE_PROPOSAL", "1.0.0", canonical_payload(payload))
    return payload, digest


def object_merge_proposal_result(
    request: MergeProposeInput, payload: dict[str, JsonValue], digest: str
) -> dict[str, JsonValue]:
    return {
        "merge_proposal": {**payload, "digest": digest},
        "lineage_preview": [f"SUPERSEDES:{item}" for item in request.object_ids],
        "impact_preview": ["application belongs to Revision namespace"],
    }
