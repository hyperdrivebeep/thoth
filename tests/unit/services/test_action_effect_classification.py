"""A forbidden (R4) effect is classified before the declaration is checked for completeness."""

from __future__ import annotations

import pytest

from thoth.application.reducers.authority_router import classify_authority
from thoth.application.services.action_service import (
    ActionService,
    checked_effect_vector,
    classify_effect_vector,
)
from thoth.domain.action import PROHIBITED_EFFECT_KEYS, ActionRiskFacts
from thoth.domain.enums import RiskTier

FORBIDDEN = (
    "changes_official_kpi",
    "grants_waiver",
    "changes_safety_threshold",
    "finalizes_model_weights",
)
PROHIBITED = (
    "R4",
    "PROHIBITED",
    ("PROHIBITED_SEMANTIC_AUTHORITY",),
    ("institution-authority",),
)
UNDEFINED = ("R3", "POLICY_UNDEFINED", ("EFFECT_COMPLETENESS_REVIEW",), ("effect-owner",))


def test_both_classifiers_use_the_same_four_forbidden_effects() -> None:
    assert set(PROHIBITED_EFFECT_KEYS) == set(FORBIDDEN)
    assert set(PROHIBITED_EFFECT_KEYS) <= set(ActionRiskFacts.model_fields)


@pytest.mark.parametrize("key", FORBIDDEN)
@pytest.mark.parametrize("completeness", [{}, {"effect_completeness_confirmed": False}])
def test_a_forbidden_effect_is_r4_even_when_the_declaration_is_not_complete(
    key: str, completeness: dict[str, object]
) -> None:
    assert classify_effect_vector({**completeness, key: True}) == PROHIBITED


@pytest.mark.parametrize("key", FORBIDDEN)
def test_a_forbidden_effect_is_r4_when_the_declaration_is_complete(key: str) -> None:
    assert classify_effect_vector({"effect_completeness_confirmed": True, key: True}) == PROHIBITED


@pytest.mark.parametrize("key", FORBIDDEN)
def test_the_deterministic_router_agrees_for_each_forbidden_effect(key: str) -> None:
    assert classify_authority(ActionRiskFacts(**{key: True})).risk_tier is RiskTier.R4


@pytest.mark.parametrize(
    "effect",
    [
        {},
        {"effect_completeness_confirmed": False},
        {"effect_completeness_confirmed": False, "external_write": True},
        {"effect_completeness_confirmed": False, "changes_official_kpi": False},
    ],
)
def test_without_a_forbidden_effect_an_incomplete_declaration_stays_r3_policy_undefined(
    effect: dict[str, object],
) -> None:
    assert classify_effect_vector(effect) == UNDEFINED


@pytest.mark.parametrize(
    ("effect", "expected"),
    [
        ({"effect_completeness_confirmed": True}, ("R0", "AUTO_ALLOWED")),
        (
            {"effect_completeness_confirmed": True, "changes_local_draft": True},
            ("R1", "PREAUTHORIZED"),
        ),
        ({"effect_completeness_confirmed": True, "sandbox_required": True}, ("R2", "AUTO_ALLOWED")),
        (
            {"effect_completeness_confirmed": True, "runs_untrusted_code": True},
            ("R2", "AUTO_ALLOWED"),
        ),
        (
            {"effect_completeness_confirmed": True, "external_write": True},
            ("R3", "APPROVAL_REQUIRED"),
        ),
        (
            {"effect_completeness_confirmed": True, "physical_action": True},
            ("R3", "APPROVAL_REQUIRED"),
        ),
    ],
)
def test_complete_declarations_keep_their_tiers(
    effect: dict[str, object], expected: tuple[str, str]
) -> None:
    assert classify_effect_vector(effect)[:2] == expected


BAD_VALUES = ["yes", "true", "no", "", 1, 0, [], {}, None]
BOOLEAN_KEYS = (
    *FORBIDDEN,
    "effect_completeness_confirmed",
    "external_write",
    "physical_action",
    "changes_official_baseline",
    "operational_equipment_change",
    "runs_untrusted_code",
    "sandbox_required",
    "changes_local_draft",
)


@pytest.mark.parametrize("key", BOOLEAN_KEYS)
@pytest.mark.parametrize("bad", BAD_VALUES, ids=repr)
def test_a_value_that_is_not_true_or_false_is_refused_instead_of_classified(
    key: str, bad: object
) -> None:
    with pytest.raises(ValueError, match=key):
        checked_effect_vector({"effect_completeness_confirmed": True, key: bad})


@pytest.mark.parametrize("bad", BAD_VALUES, ids=repr)
def test_a_bad_completeness_value_given_beside_the_effect_vector_is_refused_too(
    bad: object,
) -> None:
    with pytest.raises(ValueError, match="effect_completeness_confirmed"):
        ActionService._effect_vector({"effect_completeness_confirmed": bad, "effect_vector": {}})


def test_true_false_and_a_missing_key_are_accepted_and_kept_as_given() -> None:
    effect = checked_effect_vector(
        {
            "effect_completeness_confirmed": True,
            "grants_waiver": False,
            "cost": "UNRESOLVED",
            "data": "WRITE",
        }
    )
    assert effect == {
        "effect_completeness_confirmed": True,
        "grants_waiver": False,
        "cost": "UNRESOLVED",
        "data": "WRITE",
    }
    assert ActionService._effect_vector({})["effect_completeness_confirmed"] is False
    assert (
        ActionService._effect_vector({"effect_completeness_confirmed": True})[
            "effect_completeness_confirmed"
        ]
        is True
    )


def test_the_refusal_names_the_key_and_does_not_repeat_the_value() -> None:
    with pytest.raises(ValueError) as caught:
        checked_effect_vector({"grants_waiver": "SECRET-TEXT"})
    assert "grants_waiver" in str(caught.value) and "SECRET-TEXT" not in str(caught.value)


@pytest.mark.parametrize("key", FORBIDDEN)
def test_a_plan_step_with_a_badly_typed_forbidden_effect_is_refused_not_normalized(
    key: str,
) -> None:
    class Ids:
        def new(self, prefix: str) -> str:
            return prefix + ":1"

    service = ActionService.__new__(ActionService)
    service._ids = Ids()  # type: ignore[assignment]
    step = {
        "step_id": "step:1",
        "effect_vector": {"effect_completeness_confirmed": True, key: "yes"},
    }
    with pytest.raises(ValueError, match=key):
        service._normalize_step("project:1", step)
    ok = service._normalize_step(
        "project:1",
        {"step_id": "step:1", "effect_vector": {"effect_completeness_confirmed": True, key: True}},
    )
    assert ok["risk_tier"] == "R4"
