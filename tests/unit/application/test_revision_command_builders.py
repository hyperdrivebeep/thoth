"""Pure revision command builders retain diagnostic and response contracts without I/O."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from thoth.application.commands import revision_command_drafts as drafts
from thoth.application.commands import revision_command_views as views
from thoth.application.commands.revisions_inputs import (
    BaselinePrepareInput,
    BranchCreateInput,
    ChangeSetCreateInput,
    ProposeInput,
    RecomputeRequestInput,
)
from thoth.domain.canonical import canonical_payload, domain_digest, head_set_digest
from thoth.domain.control_record import ControlRecord
from thoth.domain.enums import EntityType


def record(payload: dict[str, object] | None = None) -> ControlRecord:
    return ControlRecord(
        control_revision_id="cr:unit",
        project_id="project:unit",
        namespace="REVISION",
        record_type="CHANGE_SET",
        record_id="record:unit",
        version=1,
        state="STAGED",
        payload=payload or {},
        record_digest="d" * 64,
        created_at=datetime(2026, 10, 15, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    "present,optimistic,branch,ready",
    [
        (True, True, False, True),
        (True, False, True, True),
        (True, False, False, False),
        (False, True, True, False),
        (False, False, False, False),
    ],
)
def test_validation_diagnostics_use_caller_judgments_and_do_not_override_missing_schema(
    present: bool, optimistic: bool, branch: bool, ready: bool
) -> None:
    checks, actual_ready, bundle, digest = drafts.validation_summary(
        (record() if present else None,), optimistic, branch
    )
    assert checks["optimistic"] is optimistic
    assert checks["branch_preservation"] is branch
    assert checks["schema"] is present and actual_ready is ready
    assert bundle["checks"] == checks
    assert digest == domain_digest("REVISION_VALIDATION_BUNDLE", "1.0.0", canonical_payload(bundle))
    response = views.validation_response(record(), checks, bundle, digest)
    assert response["validation_bundle_digest"] == digest
    assert (
        response["state"] == "STAGED"
    )  # view reports the caller's record; it does not advance it.


@pytest.mark.parametrize("missing", [(), ("f" * 64,)])
def test_change_set_payload_binds_head_set_and_preserves_missing_refs(
    missing: tuple[str, ...],
) -> None:
    request = ChangeSetCreateInput(
        project_id="project:unit",
        expected_head_set={"ACTION:a": "a" * 64},
        candidate_revision_digests=("f" * 64,),
        transition_reason="reason",
        impact_policy_ref="policy",
    )
    payload = drafts.change_set_payload(request, missing)
    assert payload["expected_head_set_digest"] == head_set_digest(request.expected_head_set)
    assert payload["missing_dependencies"] == missing
    assert payload["conflict_state"] == ("OPEN_CONFLICT" if missing else "NONE")
    assert payload["validation_bundle"] is None and payload["commit_result"] is None
    response = views.change_set_created(record(payload), missing, payload)
    assert response["missing_dependencies"] == list(missing)


def test_proposal_fields_do_not_capture_authentication_or_read_impact() -> None:
    request = ProposeInput(
        project_id="project:unit",
        aggregate_id="object",
        aggregate_type=EntityType.DECISION_OBJECT,
        parent_revision_digests=("a" * 64,),
        candidate_content={"value": "draft"},
        reason="reason",
        evidence_refs=(),
        actor_or_agent_ref="agent:unit",
        expected_head_digest="a" * 64,
    )
    fields = drafts.proposal_fields(request, {"value": "draft"}, "b" * 64)
    assert fields["actor_or_agent_ref"] == request.actor_or_agent_ref
    assert fields["candidate_content"] == {"value": "draft"}
    assert fields["candidate_schema_version"] == request.candidate_schema_version
    assert not any(k.startswith("authenticated_") for k in fields)
    assert "validation_state" not in fields
    projection = views.proposal_status_fields(record(fields), "b" * 64)
    assert projection["staged_snapshot"] == {"content_digest": "b" * 64, "canonical": False}
    assert "impact_preview" not in projection


def test_validated_payload_preserves_prior_values_without_mutation() -> None:
    current = record({"kept": "value", "validation_bundle": None})
    bundle: dict[str, object] = {"checks": {"schema": True}}
    before = current.model_dump_json()
    payload = drafts.validated_payload(current, bundle, "b" * 64)
    assert current.model_dump_json() == before
    assert payload["kept"] == "value"
    assert payload["validation_bundle"] == bundle
    assert payload["validation_bundle_digest"] == "b" * 64


def test_branch_payload_uses_bound_actor_and_does_not_mutate_head() -> None:
    request = BranchCreateInput(
        project_id="project:unit",
        aggregate_id="object",
        from_revision_digest="a" * 64,
        purpose="branch",
        actor_or_agent_ref="agent:requested",
    )
    payload = drafts.branch_payload(request, "human:bound")
    assert payload["actor_or_agent_ref"] == "human:bound"
    assert payload["canonical_head_unchanged"] is True
    assert payload["from_revision_digest"] == request.from_revision_digest


def test_recompute_checkpoint_keeps_requested_refs_and_sorts_dependency_order_only() -> None:
    request = RecomputeRequestInput(
        project_id="project:unit",
        change_set_id="change",
        projection_refs=("z", "a", "a"),
        recompute_policy_ref="policy",
    )
    payload = drafts.recompute_payload(request)
    assert payload["projection_refs"] == ("z", "a", "a")
    assert payload["dependency_order"] == ("a", "a", "z")
    assert payload["checkpoint"] == domain_digest(
        "RECOMPUTE_CHECKPOINT",
        "1.0.0",
        canonical_payload({"change_set_id": "change", "projection_refs": request.projection_refs}),
    )


def test_baseline_candidate_response_requires_separate_authority_package() -> None:
    request = BaselinePrepareInput(
        project_id="project:unit",
        purpose="candidate",
        scoped_head_map={"ACTION:a": "a" * 64},
        policy_version="policy",
        evidence_refs=(),
    )
    payload = drafts.baseline_candidate_payload(request, "b" * 64)
    assert payload["required_roles"] == ("baseline-owner",)
    assert payload["protected_authority"] is True
    candidate = record(payload)
    response = views.baseline_candidate_response(candidate, request)
    assert response["impact"] == ["ACTION:a"]
    assert response["protected_authority_package"] == {
        "approved_digest": candidate.record_digest,
        "required_roles": ["baseline-owner"],
    }
