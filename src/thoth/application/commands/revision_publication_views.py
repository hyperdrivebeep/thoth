"""Pure publication views of already-read values; preserve public and legacy response contracts."""

from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.domain.baseline import BaselineDecision, BaselineSet
from thoth.domain.control_record import ControlRecord
from thoth.domain.revision import (
    EntitySnapshot,
    SemanticMergeReceipt,
    SemanticMergeResult,
    SemanticRevision,
)


def content_response(snapshot: EntitySnapshot, revision: SemanticRevision) -> dict[str, JsonValue]:
    return {
        "snapshot": snapshot.model_dump(mode="json"),
        "schema_version": snapshot.schema_version,
        "lineage_refs": [revision.revision_digest],
    }


def restore_preview_response(
    snapshot: EntitySnapshot,
    current: SemanticRevision,
    target: SemanticRevision,
    affected: tuple[str, ...],
) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "proposed_restored_content": snapshot.content,
            "new_parent": current.revision_digest,
            "restores_revision_digest": target.revision_digest,
            "dependent_impact": list(affected),
            "protected_boundary_warning": "external effects are not rolled back",
            "change_set_candidate": True,
        },
    )


def merge_response(
    record: ControlRecord,
    conflict: ControlRecord | None,
    assessment: SemanticMergeResult,
    receipt: SemanticMergeReceipt,
) -> dict[str, JsonValue]:
    return {
        "merge_proposal": record.model_dump(mode="json"),
        "conflict": None if conflict is None else conflict.model_dump(mode="json"),
        "semantic_merge": assessment.model_dump(mode="json"),
        "receipt": receipt.model_dump(mode="json"),
        "head_mutated": assessment.head_mutated,
    }


def resolved_response(
    resolved: ControlRecord, revision: ControlRecord, change_set: ControlRecord
) -> dict[str, JsonValue]:
    return {
        "merge_proposal": resolved.model_dump(mode="json"),
        "candidate_revision": revision.model_dump(mode="json"),
        "change_set": change_set.model_dump(mode="json"),
        "head_mutated": False,
    }


def typed_baseline_response(
    decision: BaselineDecision, baseline: BaselineSet | None
) -> dict[str, JsonValue]:
    return {
        "typed_baseline_decision": decision.model_dump(mode="json"),
        "typed_baseline_set": (None if baseline is None else baseline.model_dump(mode="json")),
        "protected_decision": True,
        "current_state_mutated": baseline is not None,
    }


def baseline_response(baseline: ControlRecord) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "baseline": baseline.model_dump(mode="json"),
            "immutable_manifest": baseline.payload.get("scoped_head_map"),
        },
    )
