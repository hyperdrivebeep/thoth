"""Pure DecisionObject response projections; reads and authority stay in the facade."""

from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.application.commands.object_inputs import ObjectListInput
from thoth.application.services.revision_service import CommitResult
from thoth.domain.decision_object_full import DecisionObjectRecord, ObjectRelationRecord
from thoth.domain.enums import ImpactStatus


def object_summary(item: DecisionObjectRecord) -> dict[str, JsonValue]:
    return {
        "object_id": item.object_id,
        "project_id": item.project_id,
        "thread_id": item.thread_id,
        "purpose_statement": item.purpose_statement,
        "lifecycle": item.lifecycle,
        "active_work_mode": item.active_work_mode,
        "profile_refs": list(item.profile_refs),
        "profile_state": item.profile_state,
        "attention": item.attention,
        "blockers": list(item.blockers),
        "resolution": item.resolution,
        "verification": item.verification,
        "disposition": item.disposition,
        "freshness": item.freshness,
        "working_head_digest": item.revision_digest,
    }


def object_revision_result(
    before: DecisionObjectRecord, after: DecisionObjectRecord, commit: CommitResult
) -> dict[str, JsonValue]:
    before_data = before.model_dump(mode="json")
    after_data = after.model_dump(mode="json")
    diff: list[JsonValue] = [
        cast(
            JsonValue,
            {
                "path": key,
                "before": before_data.get(key),
                "after": after_data.get(key),
            },
        )
        for key in sorted(set(before_data) | set(after_data))
        if before_data.get(key) != after_data.get(key)
    ]
    commit_value = cast(JsonValue, commit.model_dump(mode="json"))
    return cast(
        dict[str, JsonValue],
        {
            "object": after.model_dump(mode="json"),
            "semantic_diff": diff,
            "affected_relations": list(after.relation_refs),
            "invalidations": [],
            "status_axes": {
                "lifecycle": after.lifecycle,
                "resolution": after.resolution,
                "verification": after.verification,
                "disposition": after.disposition,
                "freshness": after.freshness,
                "attention": after.attention,
            },
            "commit": commit_value,
        },
    )


def object_matches(item: DecisionObjectRecord, request: ObjectListInput) -> bool:
    return (
        (request.thread_id is None or item.thread_id == request.thread_id)
        and (request.lifecycle is None or item.lifecycle == request.lifecycle)
        and (request.active_work_mode is None or item.active_work_mode == request.active_work_mode)
        and (request.profile_ref is None or request.profile_ref in item.profile_refs)
        and (request.attention is None or item.attention == request.attention)
        and (request.blocker is None or request.blocker in item.blockers)
    )


def object_impact_view(
    source_ref: str,
    relations: tuple[ObjectRelationRecord, ...],
    downstream: tuple[str, ...],
    states: dict[str, ImpactStatus],
) -> dict[str, JsonValue]:
    return {
        "impact": {
            "source_ref": source_ref,
            "direct_relation_targets": [
                relation.target_ref for relation in relations if relation.active
            ],
            "transitive_affected_refs": list(downstream),
            "dependency_states": {
                key: state.value for key, state in states.items() if key in downstream
            },
            "recalculation_plan": [key for key in downstream if states.get(key) is not None],
        }
    }
