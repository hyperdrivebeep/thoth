"""Freeze action policy ordering and derived plan values before extracting calculations."""

from copy import deepcopy
from typing import cast

import pytest

from thoth.application.services import action_effect_vector, action_service
from thoth.application.services.action_service import ActionService, classify_effect_vector


class CalculationAccess(ActionService):
    """Expose the existing protected calculation methods without opening stores."""

    @classmethod
    def effect(cls, specification: dict[str, object]) -> dict[str, object]:
        return cls._effect_vector(specification)

    @classmethod
    def impact(cls, effect: dict[str, object]) -> dict[str, object]:
        return cls._impact(effect)

    def derive(
        self, steps: tuple[dict[str, object], ...], edges: tuple[dict[str, str], ...]
    ) -> dict[str, object]:
        return self._derive_plan(steps, edges)


def test_combined_effects_preserve_tier_precedence_and_role_order() -> None:
    effect: dict[str, object] = {
        "physical_action": True,
        "external_write": True,
        "sandbox_required": True,
    }
    assert classify_effect_vector(effect) == (
        "R3",
        "POLICY_UNDEFINED",
        ("EFFECT_COMPLETENESS_REVIEW",),
        ("effect-owner",),
    )
    effect["effect_completeness_confirmed"] = True
    assert classify_effect_vector(effect) == (
        "R3",
        "APPROVAL_REQUIRED",
        ("PROTECTED_ACTION", "ACTION_TIME_PREFLIGHT"),
        ("project-owner", "safety-owner", "external-interface-owner"),
    )
    effect.update(grants_waiver=True, effect_completeness_confirmed=False)
    assert classify_effect_vector(effect) == (
        "R4",
        "PROHIBITED",
        ("PROHIBITED_SEMANTIC_AUTHORITY",),
        ("institution-authority",),
    )


def test_effect_vector_uses_the_original_checker_and_exact_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = action_effect_vector.checked_effect_vector
    assert action_service.checked_effect_vector is original
    calls: list[tuple[object, object]] = []

    def checked(raw: object, completeness: object) -> dict[str, object]:
        calls.append((raw, completeness))
        return original(raw, completeness)

    monkeypatch.setattr(action_service, "checked_effect_vector", checked)
    raw: dict[str, object] = {"effect_completeness_confirmed": False, "cost": None}
    specification: dict[str, object] = {"effect_completeness_confirmed": True, "effect_vector": raw}
    before = deepcopy(specification)
    assert CalculationAccess.effect(specification) == raw
    assert calls == [(raw, True)]
    assert calls[0][0] is raw
    assert CalculationAccess.effect({}) == {"effect_completeness_confirmed": False}
    assert calls[-1] == ({}, False)
    assert specification == before
    with pytest.raises(ValueError) as caught:
        CalculationAccess.effect({"effect_vector": {"grants_waiver": "SECRET-TEXT"}})
    assert str(caught.value) == "effect_vector.grants_waiver must be true or false"


@pytest.mark.parametrize("effect", [{}, {"external_write": True, "cost": None}])
def test_impact_keeps_all_fields_defaults_and_explicit_none(effect: dict[str, object]) -> None:
    expected: dict[str, object] = {
        "data": "NONE",
        "code": "NONE",
        "configuration": "NONE",
        "equipment": "NONE",
        "external_institution": False,
        "baseline": False,
        "security": "NONE",
        "privacy": "NONE",
        "safety": "NONE",
        "legal": "NONE",
        "cost": "UNRESOLVED",
        "time": "UNRESOLVED",
        "observability": "UNRESOLVED",
    }
    if effect:
        expected.update(external_institution=True, cost=None)
    before = deepcopy(effect)
    actual = CalculationAccess.impact(effect)
    assert actual == expected
    assert list(actual) == list(expected)
    assert effect == before


def test_plan_frontier_unions_and_irreversibility_preserve_input_order_without_mutation() -> None:
    steps: tuple[dict[str, object], ...] = (
        {
            "step_id": "z",
            "state": "READY",
            "risk_tier": "R2",
            "policy_state": "AUTO_ALLOWED",
            "required_processes": ("P2", "P1"),
            "required_roles": ("owner-b", "owner-a"),
            "effect_vector": {"irreversible": True},
            "impact": {"data": "WRITE"},
        },
        {"step_id": "a", "state": "READY", "risk_tier": "R0", "policy_state": "AUTO_ALLOWED"},
        {
            "step_id": "blocked",
            "state": "READY",
            "risk_tier": "R3",
            "policy_state": "APPROVAL_REQUIRED",
            "required_processes": ["P1", "P3"],
            "required_roles": ["owner-a", "owner-c"],
        },
        {
            "step_id": "done",
            "state": "COMPLETE",
            "risk_tier": "R1",
            "policy_state": "PREAUTHORIZED",
            "effect_vector": {"irreversible": 1},
        },
        {
            "step_id": "unknown",
            "state": "READY",
            "risk_tier": "R0",
            "policy_state": "POLICY_UNDEFINED",
        },
        {
            "step_id": "first",
            "state": "READY",
            "risk_tier": "R1",
            "policy_state": "PREAUTHORIZED",
            "required_roles": [7, "owner-b"],
            "required_processes": "not-a-list",
            "effect_vector": "not-a-dict",
        },
    )
    edges = ({"from": "z", "to": "a"},)
    before = deepcopy((steps, edges))
    service = CalculationAccess.__new__(CalculationAccess)
    actual = service.derive(steps, edges)
    assert actual == {
        "cumulative_impact": {
            "per_step": {
                "z": {"data": "WRITE"},
                "a": {},
                "blocked": {},
                "done": {},
                "unknown": {},
                "first": {},
            },
            "max_risk_tier_display_only": "R3",
        },
        "required_process_union": ("P2", "P1", "P3"),
        "required_role_union": ("owner-b", "owner-a", "owner-c", "7"),
        "auto_executable_frontier": ("z", "first"),
        "point_of_no_return_steps": ("z",),
    }
    cumulative = cast(dict[str, object], actual["cumulative_impact"])
    per_step = cast(dict[str, object], cumulative["per_step"])
    assert list(per_step) == ["z", "a", "blocked", "done", "unknown", "first"]
    assert per_step["z"] is steps[0]["impact"]
    assert (steps, edges) == before


def test_empty_plan_preserves_default_shape() -> None:
    service = CalculationAccess.__new__(CalculationAccess)
    assert service.derive((), ()) == {
        "cumulative_impact": {"per_step": {}, "max_risk_tier_display_only": "R0"},
        "required_process_union": (),
        "required_role_union": (),
        "auto_executable_frontier": (),
        "point_of_no_return_steps": (),
    }
