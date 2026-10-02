from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import cast

import pytest
from pydantic import ValidationError

from thoth.domain.action import (
    ActionCandidate,
    ActionDraft,
    ActionRiskFacts,
    EffortEstimateDraft,
    OrdinalEstimate,
)
from thoth.domain.effort_bands import (
    EFFORT_BAND_PROFILE_V1,
    ai_estimates_from_draft,
    needs_confirmation,
    partition_by_band,
)
from thoth.domain.enums import (
    ActionState,
    ExecutionAuthority,
    Reversibility,
    RiskTier,
)


def _draft(estimates: tuple[EffortEstimateDraft, ...]) -> ActionDraft:
    return ActionDraft(
        action_id="action:1",
        object_id="object:1",
        hypothesis_ids=("hypothesis:1",),
        action_family="READ_ONLY_ANALYSIS",
        specification="bounded check",
        expected_information_value="separates explanations",
        reversibility=Reversibility.FULL,
        effect_facts=ActionRiskFacts(),
        effect_completeness_confirmed=True,
        source_refs=("span:1",),
        effort_estimates=estimates,
    )


def test_profile_records_the_reviewed_band_meanings_without_numbers() -> None:
    profile = EFFORT_BAND_PROFILE_V1
    assert (profile.profile_id, profile.version) == ("effort-band-profile", "1.0.0")
    bands = {(item.dimension, item.band) for item in profile.definitions}
    assert bands == {
        (dimension, band)
        for dimension in ("TIME", "COST_EFFORT")
        for band in ("LOW", "MEDIUM", "HIGH", "UNKNOWN")
    }
    # The task-type trigger is applied before any duration example; durations are references only.
    assert profile.absolute_triggers and profile.boundary_rules
    assert any("먼저" in rule for rule in profile.boundary_rules)
    assert any("독립" in rule for rule in profile.boundary_rules)

    def numbers(value: object) -> list[object]:
        if isinstance(value, bool):
            return []
        if isinstance(value, int | float):
            return [value]
        if isinstance(value, dict):
            children = list(cast(dict[str, object], value).values())
        elif isinstance(value, list | tuple):
            children = list(cast(list[object], value))
        else:
            return []
        return [n for child in children for n in numbers(child)]

    assert numbers(profile.model_dump(mode="json")) == []


def test_estimate_without_basis_cannot_carry_a_band() -> None:
    with pytest.raises(ValidationError):
        OrdinalEstimate(
            dimension="TIME",
            band="MEDIUM",
            estimator_type="AI",
            estimator_ref="ACTION_PLANNER",
            basis_text="  ",
            profile_id="effort-band-profile",
            profile_version="1.0.0",
        )
    unknown = OrdinalEstimate(
        dimension="TIME",
        band="UNKNOWN",
        estimator_type="AI",
        estimator_ref="ACTION_PLANNER",
        basis_text="",
        profile_id="effort-band-profile",
        profile_version="1.0.0",
    )
    assert unknown.band == "UNKNOWN"


def test_model_estimates_without_basis_or_outside_the_table_are_stored_as_unknown() -> None:
    draft = _draft(
        (
            EffortEstimateDraft(dimension="TIME", band="MEDIUM", basis_text="담당자 회신이 필요"),
            EffortEstimateDraft(dimension="COST_EFFORT", band="HIGH", basis_text=""),
            EffortEstimateDraft(dimension="TIME", band="MEDIUM", basis_text="duplicate dimension"),
            EffortEstimateDraft(dimension="RISK", band="LOW", basis_text="not a dimension"),
        )
    )
    estimates = ai_estimates_from_draft(draft.effort_estimates)
    assert [(item.dimension, item.band) for item in estimates] == [
        ("TIME", "MEDIUM"),
        ("COST_EFFORT", "UNKNOWN"),
    ]
    assert all(item.estimator_type == "AI" for item in estimates)
    assert all(
        (item.profile_id, item.profile_version) == ("effort-band-profile", "1.0.0")
        for item in estimates
    )
    weird = ai_estimates_from_draft(
        (EffortEstimateDraft(dimension="TIME", band="EXTREME", basis_text="anything"),)
    )
    assert weird[0].band == "UNKNOWN"


def test_bands_are_never_numbers_or_orderable_and_unknown_is_its_own_group() -> None:
    low = OrdinalEstimate(
        dimension="TIME",
        band="LOW",
        estimator_type="AI",
        estimator_ref="a",
        basis_text="same day",
        profile_id="effort-band-profile",
        profile_version="1.0.0",
    )
    high = low.model_copy(update={"band": "HIGH"})
    unknown = low.model_copy(update={"band": "UNKNOWN", "basis_text": ""})
    with pytest.raises(TypeError):
        sorted([high, low])  # type: ignore[type-var]
    dumped = json.dumps(low.model_dump(mode="json"))
    assert '"LOW"' in dumped and not any(char.isdigit() for char in low.band)
    groups = partition_by_band((low, unknown, high))
    assert groups.pop("NEEDS_CONFIRMATION") == (unknown,)
    assert set(groups) == {"LOW", "MEDIUM", "HIGH"} and groups["MEDIUM"] == ()
    assert needs_confirmation((low, unknown)) == (unknown,)


def test_old_one_zero_zero_candidate_reads_with_no_estimates() -> None:
    payload = {
        "action_id": "action:old",
        "object_id": "object:1",
        "hypothesis_ids": ["hypothesis:1"],
        "action_family": "READ_ONLY_ANALYSIS",
        "specification": "old",
        "expected_information_value": "old",
        "estimated_cost": None,
        "estimated_seconds": None,
        "risk_tier": RiskTier.R0.value,
        "execution_authority": ExecutionAuthority.AUTO_R0.value,
        "reversibility": Reversibility.FULL.value,
        "external_write": False,
        "sandbox_required": False,
        "state": ActionState.AUTO_ALLOWED.value,
        "required_approver_role": None,
        "source_refs": ["span:1"],
        "missing_evidence": [],
        "primary_purpose": None,
        "effect_facts": None,
        "effect_completeness_confirmed": False,
        "schema_version": "1.0.0",
    }
    old = ActionCandidate.model_validate(payload)
    assert old.effort_estimates == () and old.schema_version == "1.0.0"
    fresh = ActionCandidate.model_validate(
        {k: v for k, v in payload.items() if k != "schema_version"}
    )
    assert fresh.schema_version == "1.1.0"
    stamp = datetime(2026, 9, 30, tzinfo=UTC)
    assert stamp.tzinfo is not None
