"""Pure command response fields; contextual reads remain at their original handler sites."""

from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.application.commands.revisions_inputs import BaselinePrepareInput
from thoth.domain.control_record import ControlRecord


def proposal_status_fields(record: ControlRecord, content_digest: str) -> dict[str, JsonValue]:
    return {
        "proposal": record.model_dump(mode="json"),
        "staged_snapshot": {"content_digest": content_digest, "canonical": False},
        "validation_state": "DRAFT",
    }


def change_set_created(
    record: ControlRecord, missing: tuple[str, ...], payload: dict[str, object]
) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "change_set": record.model_dump(mode="json"),
            "missing_dependencies": list(missing),
            "conflict_state": payload["conflict_state"],
        },
    )


def validation_response(
    revised: ControlRecord,
    checks: dict[str, object],
    validation_bundle: dict[str, object],
    validation_digest: str,
) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "change_set": revised.model_dump(mode="json"),
            "checks": checks,
            "impact_propagation_plan": validation_bundle["impact_propagation_plan"],
            "state": revised.state,
            "validation_bundle_digest": validation_digest,
        },
    )


def baseline_candidate_response(
    record: ControlRecord, request: BaselinePrepareInput
) -> dict[str, JsonValue]:
    return {
        "baseline_candidate": record.model_dump(mode="json"),
        "impact": list(request.scoped_head_map),
        "protected_authority_package": {
            "approved_digest": record.record_digest,
            "required_roles": ["baseline-owner"],
        },
    }
