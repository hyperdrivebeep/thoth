"""Approvals stored before content binding keep their rule, and are never rewritten to 2.0.0."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import JsonValue

from thoth.application.services.authorization_currentness import (
    authorization_applies,
    authorization_read_view,
    stale_decisions,
)
from thoth.domain.action_full import ActionPlanRecord, AuthorizationEnvelopeRecord
from thoth.domain.protected_action import payload_digest, payload_from_step

NOW = datetime(2026, 9, 30, tzinfo=UTC)
STEP: dict[str, object] = {
    "step_id": "step:r3",
    "risk_tier": "R3",
    "required_roles": ["project-owner"],
    "channel": "EMAIL",
    "content": {"body_text": "본문"},
}


def plan_state(state: str) -> Callable[[str, str, str], dict[str, JsonValue]]:
    return lambda project_id, plan_id, digest: {"state": state}


def plan(revision: str, step: dict[str, object] | None = None) -> ActionPlanRecord:
    return ActionPlanRecord(
        plan_revision_id=f"plan-revision:{revision}",
        plan_id="plan:1",
        project_id="p",
        object_id="object:1",
        selected_action_refs=(),
        steps=(step or STEP,),
        dependency_edges=(),
        cumulative_impact={},
        required_process_union=(),
        required_role_union=(),
        auto_executable_frontier=(),
        point_of_no_return_steps=(),
        revision_digest=revision * 64,
        created_at=NOW,
    )


def envelope(**patch: Any) -> AuthorizationEnvelopeRecord:
    base: dict[str, Any] = {
        "authorization_revision_id": "auth-revision:1",
        "authorization_id": "auth:1",
        "project_id": "p",
        "plan_id": "plan:1",
        "step_id": "step:r3",
        "plan_revision_digest": "a" * 64,
        "predecessor_output_digests": (),
        "target_baseline_digests": ("b" * 64,),
        "policy_version": "policy",
        "exact_scope_digest": "c" * 64,
        "required_roles": ("project-owner",),
        "state": "APPROVED",
        "expires_at": NOW + timedelta(hours=1),
        "revision_digest": "d" * 64,
        "created_at": NOW,
    }
    return AuthorizationEnvelopeRecord.model_validate({**base, **patch})


def v2() -> AuthorizationEnvelopeRecord:
    payload = payload_from_step(STEP)
    return envelope(
        schema_version="2.0.0",
        payload=payload.model_dump(mode="json"),
        payload_digest=payload_digest(payload),
    )


def test_a_stored_1_0_0_envelope_keeps_the_plan_revision_rule_without_new_fields() -> None:
    legacy = envelope()
    assert (
        legacy.schema_version,
        legacy.payload,
        legacy.payload_digest,
        legacy.material_changes,
    ) == (
        "1.0.0",
        None,
        None,
        (),
    )
    assert authorization_applies(legacy, plan("a"), STEP) is True
    # Any plan revision, even a display-only one, ends a 1.0.0 approval as before.
    assert authorization_applies(legacy, plan("e"), STEP) is False


def test_a_1_0_0_envelope_is_never_marked_stale_or_rewritten() -> None:
    changed = {**STEP, "channel": "FORM"}
    assert stale_decisions((envelope(),), plan("e", changed)) == ()


def test_a_2_0_0_envelope_follows_the_payload_across_plan_revisions() -> None:
    item = v2()
    assert (
        authorization_applies(item, plan("e", {**STEP, "presentation": {"label": "x"}}), STEP)
        is True
    )
    assert stale_decisions((item,), plan("e", {**STEP, "presentation": {"label": "x"}})) == ()
    (decision,) = stale_decisions((item,), plan("e", {**STEP, "channel": "FORM"}))
    assert [change.label for change in decision.changes] == ["전달 경로"]
    (removed,) = stale_decisions((item,), plan("e", {**STEP, "step_id": "step:other"}))
    assert [change.label for change in removed.changes] == ["단계"]


def test_a_2_0_0_envelope_must_carry_its_payload_and_a_stale_one_its_reason() -> None:
    import pytest

    with pytest.raises(ValueError):
        envelope(schema_version="2.0.0")
    with pytest.raises(ValueError):
        envelope(state="STALE")


def test_new_evidence_alone_is_a_notice_not_a_stale_approval() -> None:
    item, current = v2(), plan("e")
    view = authorization_read_view(
        item, current, now=NOW, currentness=plan_state("REVIEW_REQUIRED")
    )
    assert view["payload_current"] is True and view["review_needed"] is True
    assert view["effective_state"] == "APPROVED"
    calm = authorization_read_view(item, current, now=NOW, currentness=plan_state("CURRENT"))
    assert calm["review_needed"] is False
    changed = authorization_read_view(
        item,
        plan("e", {**STEP, "channel": "FORM"}),
        now=NOW,
        currentness=plan_state("CURRENT"),
    )
    assert changed["payload_current"] is False and changed["review_needed"] is False
