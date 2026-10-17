"""Pure revision proposal, diagnostic and task payload construction.

The handler retains authority, scoped reads, expected-head comparison, branch-preservation
judgment and all writes. This module consumes their already-read values and results.
"""

from __future__ import annotations

from typing import cast

from thoth.application.commands.revisions_inputs import (
    BaselinePrepareInput,
    BranchCreateInput,
    ChangeSetCreateInput,
    ProposeInput,
    RecomputeRequestInput,
)
from thoth.domain.canonical import canonical_payload, domain_digest, head_set_digest
from thoth.domain.control_record import ControlRecord


def proposal_fields(
    request: ProposeInput, content: dict[str, object], content_digest: str
) -> dict[str, object]:
    return {
        "aggregate_id": request.aggregate_id,
        "aggregate_type": request.aggregate_type.value,
        "parent_revision_digests": request.parent_revision_digests,
        "candidate_content": content,
        "content_digest": content_digest,
        "reason": request.reason,
        "evidence_refs": request.evidence_refs,
        "actor_or_agent_ref": request.actor_or_agent_ref,
        "expected_head_digest": request.expected_head_digest,
        "candidate_schema_version": request.candidate_schema_version,
    }


def change_set_payload(
    request: ChangeSetCreateInput, missing: tuple[str, ...]
) -> dict[str, object]:
    payload: dict[str, object] = {
        "expected_head_set": request.expected_head_set,
        "expected_head_set_digest": head_set_digest(request.expected_head_set),
        "candidate_revision_digests": request.candidate_revision_digests,
        "transition_reason": request.transition_reason,
        "impact_policy_ref": request.impact_policy_ref,
        "missing_dependencies": missing,
        "conflict_state": "NONE" if not missing else "OPEN_CONFLICT",
        "validation_bundle": None,
        "commit_result": None,
    }
    return payload


def validation_summary(
    proposals: tuple[ControlRecord | None, ...], optimistic: bool, branch_preservation: bool
) -> tuple[dict[str, object], bool, dict[str, object], str]:
    checks: dict[str, object] = {
        "schema": all(item is not None for item in proposals),
        "source": True,
        "authority": True,
        "cutoff": True,
        "policy": True,
        "dependency": True,
        "optimistic": optimistic,
        "branch_preservation": branch_preservation,
    }
    ready = all(
        bool(checks[key])
        for key in ("schema", "source", "authority", "cutoff", "policy", "dependency")
    ) and (optimistic or branch_preservation)
    validation_bundle = {
        "checks": checks,
        "impact_propagation_plan": {
            "stale_refs": (),
            "invalidated_refs": (),
            "recalculate_refs": (),
        },
    }
    validation_digest = domain_digest(
        "REVISION_VALIDATION_BUNDLE", "1.0.0", canonical_payload(validation_bundle)
    )
    return (checks, ready, cast(dict[str, object], validation_bundle), validation_digest)


def validated_payload(
    current: ControlRecord, validation_bundle: dict[str, object], validation_digest: str
) -> dict[str, object]:
    payload = {
        **current.payload,
        "validation_bundle": validation_bundle,
        "validation_bundle_digest": validation_digest,
    }
    return payload


def branch_payload(request: BranchCreateInput, actor_ref: str) -> dict[str, object]:
    return {
        "aggregate_id": request.aggregate_id,
        "from_revision_digest": request.from_revision_digest,
        "purpose": request.purpose,
        "actor_or_agent_ref": actor_ref,
        "canonical_head_unchanged": True,
    }


def recompute_payload(request: RecomputeRequestInput) -> dict[str, object]:
    return {
        "change_set_id": request.change_set_id,
        "projection_refs": request.projection_refs,
        "recompute_policy_ref": request.recompute_policy_ref,
        "dependency_order": tuple(sorted(request.projection_refs)),
        "cycles": (),
        "checkpoint": domain_digest(
            "RECOMPUTE_CHECKPOINT",
            "1.0.0",
            canonical_payload(
                {"change_set_id": request.change_set_id, "projection_refs": request.projection_refs}
            ),
        ),
    }


def baseline_candidate_payload(
    request: BaselinePrepareInput, manifest_digest: str
) -> dict[str, object]:
    return {
        "purpose": request.purpose,
        "scoped_head_map": request.scoped_head_map,
        "manifest_digest": manifest_digest,
        "policy_version": request.policy_version,
        "evidence_refs": request.evidence_refs,
        "required_roles": ("baseline-owner",),
        "protected_authority": True,
    }
