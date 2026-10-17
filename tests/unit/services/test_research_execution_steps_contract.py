"""Freeze execution-step binding values, identity, errors and source immutability."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from thoth.application.services.action_projection import full_action
from thoth.application.services.research_identity_service import (
    ResearchContext,
    ResearchIdentityService,
)
from thoth.domain.action import ActionCandidate
from thoth.domain.action_full import ActionPlanRecord, ActionRecord
from thoth.domain.enums import ExecutionAuthority, Reversibility, RiskTier
from thoth.domain.research_identity import ResearchIdentityError
from thoth.domain.sandbox import SandboxInputSnapshot, SandboxRunSpec, SandboxRuntimeProfile

T0 = datetime(2026, 9, 1, tzinfo=UTC)


class Boundary(ResearchIdentityService):
    @staticmethod
    def bind(
        context: ResearchContext,
        action: ActionCandidate,
        spec: SandboxRunSpec,
        predictions: tuple[str, ...],
    ) -> tuple[dict[str, object], ...]:
        return ResearchIdentityService._bound_execution_steps(context, action, spec, predictions)


def candidate(
    identifier: str = "a", *, tier: RiskTier = RiskTier.R2, complete: bool = True
) -> ActionCandidate:
    return ActionCandidate(
        action_id=identifier,
        object_id="o",
        hypothesis_ids=("h:2", "h:1"),
        action_family="SANDBOX_REPLAY" if tier == RiskTier.R2 else "READ_ONLY_ANALYSIS",
        specification="fixture",
        expected_information_value="fixture",
        risk_tier=tier,
        execution_authority=ExecutionAuthority.SANDBOX_ONLY_R2
        if tier == RiskTier.R2
        else ExecutionAuthority.AUTO_R0,
        reversibility=Reversibility.FULL,
        external_write=False,
        sandbox_required=tier == RiskTier.R2,
        source_refs=("span",),
        effect_completeness_confirmed=complete,
    )


def record(action: ActionCandidate, revision: str = "rev") -> ActionRecord:
    return full_action(
        action,
        project_id="p",
        portfolio_id="portfolio",
        revision_id=revision,
        created_at=T0,
        parent=None,
    )


def fixture(missing_state: bool = False) -> tuple[ResearchContext, ActionCandidate, SandboxRunSpec]:
    action = candidate()
    selected: dict[str, object] = {
        "label": "selected",
        "policy_state": "OLD",
        "step_id": "s-a",
        "action_id": "a",
        "action_revision_digest": "old",
        "state": "READY",
        "input_digests": ("old",),
        "runtime_binding": {"old": "binding"},
    }
    if missing_state:
        del selected["state"]
    other: dict[str, object] = {
        "step_id": "s-b",
        "action_id": "b",
        "input_digests": ("kept",),
        "prediction_refs": ("prior",),
        "keep": "unchanged",
        "policy_state": "OLD",
    }
    context = ResearchContext(
        project_id="p",
        object_id="o",
        heads={},
        actions=(record(action), record(candidate("b", tier=RiskTier.R0), "other")),
        action_plan=ActionPlanRecord.model_construct(plan_id="plan", steps=(selected, other)),
    )
    spec = SandboxRunSpec(
        project_id="p",
        attempt_id="attempt",
        runtime_profile=SandboxRuntimeProfile.SCRIPTED,
        image_digest="fixture-image",
        argv=("runner", "input"),
        policy_id="policy",
        policy_revision=1,
        policy_digest="c" * 64,
        action_id="a",
        action_plan_id="plan",
        input_snapshots=(
            SandboxInputSnapshot(
                artifact_id="artifact:a", content_sha256="a" * 64, source_path="a", target_name="a"
            ),
            SandboxInputSnapshot(
                artifact_id="artifact:b", content_sha256="b" * 64, source_path="b", target_name="b"
            ),
        ),
    )
    return context, action, spec


@pytest.mark.parametrize("missing_state", [False, True])
def test_bound_steps_keep_values_key_order_references_and_prior_dicts(missing_state: bool) -> None:
    context, action, spec = fixture(missing_state)
    assert context.action_plan is not None
    before = deepcopy(context.action_plan.steps)
    before_actions = tuple(item.model_dump(mode="json") for item in context.actions)
    predictions = ("prediction:2", "prediction:1")
    steps = Boundary.bind(context, action, spec, predictions)
    a, b = context.actions
    expected: list[tuple[str, object]] = [
        ("label", "selected"),
        ("policy_state", "PREAUTHORIZED"),
        ("step_id", "s-a"),
        ("action_id", "a"),
        ("action_revision_digest", a.revision_digest),
    ]
    if not missing_state:
        expected.append(("state", "READY"))
    expected.extend(
        [
            ("input_digests", ("a" * 64, "b" * 64)),
            (
                "runtime_binding",
                {
                    "runtime_profile": "SCRIPTED",
                    "image_digest": "fixture-image",
                    "argv": spec.argv,
                    "policy_digest": "c" * 64,
                },
            ),
            ("risk_tier", "R2"),
            ("effect_vector", a.effect_vector),
            ("required_roles", a.required_roles),
            ("required_processes", a.required_processes),
            ("prediction_refs", predictions),
            ("hypothesis_refs", action.hypothesis_ids),
        ]
    )
    if missing_state:
        expected.append(("state", "READY"))
    assert list(steps[0].items()) == expected
    assert list(steps[1].items()) == [
        ("step_id", "s-b"),
        ("action_id", "b"),
        ("input_digests", ("kept",)),
        ("prediction_refs", ("prior",)),
        ("keep", "unchanged"),
        ("policy_state", b.policy_state),
        ("action_revision_digest", b.revision_digest),
        ("risk_tier", "R0"),
        ("effect_vector", b.effect_vector),
        ("required_roles", b.required_roles),
        ("required_processes", b.required_processes),
    ]
    assert steps[0]["prediction_refs"] is predictions
    assert steps[0]["hypothesis_refs"] is action.hypothesis_ids
    binding = steps[0]["runtime_binding"]
    assert isinstance(binding, dict) and binding["argv"] is spec.argv
    again = Boundary.bind(context, action, spec, predictions)
    assert steps[0]["input_digests"] == again[0]["input_digests"]
    assert steps[0]["input_digests"] is not again[0]["input_digests"]
    assert all(new is not old for new, old in zip(steps, context.action_plan.steps, strict=True))
    assert context.action_plan.steps == before
    assert tuple(item.model_dump(mode="json") for item in context.actions) == before_actions


def test_duplicate_action_id_uses_last_record() -> None:
    context, action, spec = fixture()
    last = record(action, "last-revision")
    context = replace(context, actions=(*context.actions, last))
    steps = Boundary.bind(context, action, spec, ())
    assert steps[0]["action_revision_digest"] == last.revision_digest


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("missing_selected", "TEST_EXECUTION_ACTION_BASIS_MISMATCH"),
        ("view_mismatch", "TEST_EXECUTION_ACTION_BASIS_MISMATCH"),
        ("not_r2", "TEST_EXECUTION_ACTION_BASIS_MISMATCH"),
        ("incomplete", "TEST_EXECUTION_ACTION_BASIS_MISMATCH"),
        ("unresolved_first", "TEST_EXECUTION_STEP_IDENTITY_UNRESOLVED"),
        ("not_ready_first", "TEST_EXECUTION_STEP_NOT_READY"),
        ("unresolved_after_valid", "TEST_EXECUTION_STEP_IDENTITY_UNRESOLVED"),
        ("non_string", "TEST_EXECUTION_STEP_IDENTITY_UNRESOLVED"),
        ("duplicate_selected", "TEST_EXECUTION_STEP_NOT_READY"),
        ("not_in_plan", "TEST_EXECUTION_STEP_NOT_IN_PLAN"),
    ],
)
def test_binding_error_priority(case: str, reason: str) -> None:
    context, action, spec = fixture()
    assert context.action_plan is not None
    plan = context.action_plan
    selected, other = plan.steps
    unknown = {"step_id": "bad", "action_id": "missing"}
    unready = {**selected, "state": "DONE"}
    if case == "missing_selected":
        context = replace(context, actions=(context.actions[1],))
        steps = (unknown,)
    elif case == "view_mismatch":
        action = action.model_copy(update={"specification": "changed"})
        steps = (unknown,)
    elif case in {"not_r2", "incomplete"}:
        action = candidate(
            tier=RiskTier.R0 if case == "not_r2" else RiskTier.R2, complete=case != "incomplete"
        )
        context = replace(context, actions=(record(action),))
        steps = (unknown,)
    else:
        steps = {
            "unresolved_first": (unknown, unready),
            "not_ready_first": (unready, unknown),
            "unresolved_after_valid": (selected, unknown),
            "non_string": ({"step_id": "bad", "action_id": 7},),
            "duplicate_selected": (selected, selected, unknown),
            "not_in_plan": (other,),
        }[case]
    context = replace(context, action_plan=plan.model_copy(update={"steps": steps}))
    with pytest.raises(ResearchIdentityError) as caught:
        Boundary.bind(context, action, spec, ())
    assert caught.value.reason_code == reason and str(caught.value) == reason


def test_missing_plan_asserts_before_other_errors() -> None:
    context, action, spec = fixture()
    context = replace(context, action_plan=None, actions=())
    with pytest.raises(AssertionError) as caught:
        Boundary.bind(context, action, spec, ())
    assert caught.value.args == ()
