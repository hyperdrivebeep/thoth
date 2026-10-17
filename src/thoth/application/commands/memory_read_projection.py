"""Pure policy selection and Memory projection responses; policy ownership stays in memory_full."""

from collections.abc import Mapping
from typing import cast

from pydantic import JsonValue

from thoth.application.commands.memory_inputs import PolicyListInput
from thoth.domain.control_record import ControlRecord
from thoth.domain.memory import MemoryProjection


def select_policies(
    policies: tuple[Mapping[str, object], ...], request: PolicyListInput
) -> tuple[Mapping[str, object], ...]:
    return tuple(
        policy
        for policy in policies
        if (request.payload_mode is None or policy["payload_mode"] == request.payload_mode)
        and (
            request.recall_class is None
            or request.recall_class in cast(tuple[str, ...], policy["recall_classes"])
        )
        and (not request.enabled_only or policy["enabled"] is True)
    )


def find_policy(
    policies: tuple[Mapping[str, object], ...], profile_ref: str
) -> Mapping[str, object] | None:
    return next((item for item in policies if item["profile_ref"] == profile_ref), None)


def projection_status_view(
    tasks: tuple[ControlRecord, ...], projections: tuple[MemoryProjection, ...]
) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "canonical_reference_health": "PASS",
            "index_freshness": "CURRENT" if not tasks else tasks[-1].state,
            "vector_freshness": "DERIVED_ONLY",
            "summary_freshness": "DERIVED_ONLY",
            "rebuild_checkpoints": [item.payload.get("checkpoint") for item in tasks],
            "leakage_checks": "PROJECT_FILTER_BEFORE_RERANK",
            "full_projection_types": [item.projection_type for item in projections],
            "derived_projection_canonical_truth": False,
        },
    )
