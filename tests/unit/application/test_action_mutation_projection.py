"""Pure Action response assembly after the 91 move: values, key order, copies and shared objects."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from thoth.application.commands import action_mutation_projection as projection
from thoth.application.commands.action_read_projection import plan_step
from thoth.domain.canonical import canonical_payload, domain_digest


class _Dumped:
    """A record whose JSON dump is a marker, so dumps are visible in the result."""

    def __init__(self, name: str, **fields: Any) -> None:
        self.name = name
        self.__dict__.update(fields)

    def model_dump(self, *, mode: str) -> dict[str, str]:
        return {"dumped": self.name, "mode": mode}


def fake(name: str, **fields: Any) -> Any:
    return _Dumped(name, **fields)


def ns(**fields: Any) -> Any:
    return SimpleNamespace(**fields)


def commit(name: str = "commit") -> Any:
    return fake(name)


def plan(**fields: Any) -> Any:
    values: dict[str, Any] = {
        "steps": [{"step_id": "step:a", "risk_tier": "R0", "policy_state": "POLICY_DEFINED"}],
        "scenario_results": ["gate"],
        "cumulative_impact": {"per_step": {"step:a": {}}},
        "required_process_union": ("review",),
        "required_role_union": ("owner",),
        "auto_executable_frontier": ("step:a",),
        "point_of_no_return_steps": (),
        "authorization_refs": ("authorization:1",),
        "dependency_edges": ({"from": "step:a", "to": "step:b"},),
        "validation_state": "VALIDATED",
    }
    return fake("plan", **{**values, **fields})


def keys(result: dict[str, Any]) -> list[str]:
    return list(result)


def test_create_result_order_missing_fields_and_copies() -> None:
    duplicates = ("action:1", "action:2")
    item = fake(
        "action",
        risk_tier="R0",
        policy_state="DEFINED",
        specification={"missing_fields": ("a", 2)},
    )
    result = projection.create_result(item, duplicates, commit())
    assert keys(result) == [
        "action",
        "impact_policy_precheck",
        "missing_fields",
        "duplicate_candidates",
        "commit",
    ]
    assert result["missing_fields"] == ["a", "2"]
    assert result["duplicate_candidates"] == ["action:1", "action:2"]
    assert result["impact_policy_precheck"] == {"risk_tier": "R0", "policy_state": "DEFINED"}
    for specification in ({}, {"missing_fields": ["a"]}):
        other = fake("action", risk_tier="R1", policy_state="X", specification=specification)
        assert projection.create_result(other, (), commit())["missing_fields"] == []


def test_portfolio_results() -> None:
    portfolio = fake(
        "portfolio",
        scenario_results=["a", "b"],
        uncertainty={"u": 1},
        sensitivity={"s": 2},
        value_of_information={"v": 3},
        decision_state="STABLE",
        recommendation_ref="action:9",
    )
    composed = projection.portfolio_compose_result(portfolio, commit())
    assert composed == {
        "portfolio": {"dumped": "portfolio", "mode": "json"},
        "missing_alternatives": [],
        "missing_criteria": [],
        "commit": {"dumped": "commit", "mode": "json"},
    }
    evaluated = projection.portfolio_evaluate_result(portfolio, commit())
    assert keys(evaluated) == [
        "portfolio",
        "mandatory_gate_results",
        "comparison",
        "uncertainty",
        "sensitivity",
        "value_of_information",
        "rank_stability",
        "commit",
    ]
    assert evaluated["mandatory_gate_results"] == evaluated["comparison"] == ["a", "b"]
    assert evaluated["mandatory_gate_results"] is not evaluated["comparison"]
    assert evaluated["uncertainty"] is portfolio.uncertainty
    assert evaluated["rank_stability"] == "STABLE"
    recommended = projection.recommend_result(portfolio, commit())
    assert recommended["recommendation"] == "action:9"
    assert recommended["selection_implied"] is False
    assert recommended["authorization_implied"] is False
    assert recommended["sensitivity"] is portfolio.sensitivity


def test_select_result_shares_the_decision_context() -> None:
    context = {"reason": "pick"}
    request = ns(decision_context=context)
    result = projection.select_result(
        request,
        fake("action"),
        fake("portfolio"),
        commit("a"),
        commit("p"),
    )
    assert result["selection_rationale"] is context
    assert result["authorization_implied"] is False
    assert result["commits"] == [
        {"dumped": "a", "mode": "json"},
        {"dumped": "p", "mode": "json"},
    ]


def test_plan_results_copy_sequences_and_share_mappings() -> None:
    revised = plan()
    composed = projection.plan_compose_result(revised, commit())
    assert composed["initial_frontier"] == ["step:a"]
    assert composed["profile_schema_impact_policy_validation"] == "VALIDATED"
    revalidated = projection.plan_revalidate_result(revised, ns(trigger_reason="why"), commit())
    assert keys(revalidated) == [
        "plan",
        "dependency",
        "impact",
        "policy",
        "frontier",
        "point_of_no_return",
        "authorization",
        "trigger_reason",
        "commit",
    ]
    assert revalidated["impact"] is revised.cumulative_impact
    assert revalidated["policy"] == ["review"] and revalidated["trigger_reason"] == "why"
    impact = projection.impact_recalculate_result(revised, ns(trigger_refs=("span:1",)), commit())
    assert impact["per_step_effects"] is revised.cumulative_impact["per_step"]
    assert impact["trigger_refs"] == ["span:1"] and impact["risk_changes"] == []
    bare = projection.impact_recalculate_result(
        plan(cumulative_impact={}),
        ns(trigger_refs=()),
        commit(),
    )
    assert bare["per_step_effects"] == {}


def test_step_results_use_the_plan_step_lookup() -> None:
    revised = plan()
    added = projection.step_add_result(revised, "step:a", commit())
    assert added["step"] is plan_step(revised, "step:a")
    assert keys(added) == ["plan", "step", "frontier_delta", "commit"]
    revised_step = projection.step_revise_result(revised, ns(step_id="step:a"), commit())
    assert revised_step["step"] is revised.steps[0]
    assert revised_step["invalidated_authorizations_executions"] == (
        "ALL_BOUND_TO_PREVIOUS_PLAN_REVISION"
    )
    with pytest.raises(Exception, match=r"not found|absent|step"):
        projection.step_add_result(revised, "step:missing", commit())


def test_policy_classify_result() -> None:
    revised = plan(
        steps=[
            {"step_id": "step:a", "risk_tier": "R0", "policy_state": "POLICY_DEFINED"},
            {"step_id": "step:b", "policy_state": "POLICY_UNDEFINED"},
        ]
    )
    result = projection.policy_classify_result(revised, ns(policy_version=None), commit())
    assert result["risk_tiers"] == {"step:a": "R0", "step:b": None}
    assert result["policy_states"] == {
        "step:a": "POLICY_DEFINED",
        "step:b": "POLICY_UNDEFINED",
    }
    assert result["unknown_mappings"] == ["step:b"]
    assert result["policy_version"] == "project-policy:current"
    named = projection.policy_classify_result(revised, ns(policy_version="policy:x"), commit())
    assert named["policy_version"] == "policy:x"


def test_authorization_results() -> None:
    request = ns(predecessor_output_digests=("a" * 64,), target_baseline_digests=("b" * 64,))
    prepared = projection.authorization_prepare_result(fake("authorization"), "PENDING", request)
    assert keys(prepared) == ["authorization", "state", "preflight_requirements"]
    assert prepared["preflight_requirements"] == {
        "predecessor_outputs": ["a" * 64],
        "target_baselines": ["b" * 64],
    }
    assert (
        projection.authorization_prepare_result(None, "NOT_REQUIRED", request)["authorization"]
        is None
    )
    decided = fake("authorization", exact_scope_digest="c" * 64, single_use=True)
    assert projection.authorization_decide_result(decided) == {
        "authorization": {"dumped": "authorization", "mode": "json"},
        "exact_scope_digest": "c" * 64,
        "single_use": True,
    }


def test_compensation_specification_and_result() -> None:
    refs = ("effect:1",)
    request = ns(
        intended_mitigation="mitigate",
        caused_by_execution_attempt_ref="attempt:1",
        observed_effect_refs=refs,
        residual_effect_expectation="residual",
    )
    specification = projection.compensation_specification(request)
    assert specification == {
        "description": "mitigate",
        "caused_by_execution_attempt_ref": "attempt:1",
        "observed_effect_refs": refs,
        "expected_observation_or_change": {"residual_effect_expectation": "residual"},
        "effect_completeness_confirmed": False,
        "effect_vector": {"effect_completeness_confirmed": False},
        "stop_conditions": ("mitigation boundary reached",),
        "observability": "residual effect evidence required",
    }
    assert specification["observed_effect_refs"] is refs
    result = projection.compensation_result(request, fake("action"), commit())
    assert result == {
        "action": {"dumped": "action", "mode": "json"},
        "exact_rollback_claimed": False,
        "residual_risk_contract": "residual",
        "commit": {"dumped": "commit", "mode": "json"},
    }


@pytest.mark.parametrize(
    ("purposes", "conflicts"), [({"A"}, []), ({"A", "B"}, ["primary_purpose"])]
)
def test_merge_proposal_payload_digest_and_result(purposes: set[str], conflicts: list[str]) -> None:
    mapping = {"primary_purpose": "retain both"}
    request = ns(
        project_id="project:u",
        action_ids=("action:1", "action:2"),
        field_mapping=mapping,
        evidence_refs=("span:1",),
        rationale="why",
        expected_revision_digests=("d" * 64, "e" * 64),
    )
    payload, digest = projection.merge_proposal_payload(request, purposes)
    assert list(payload) == [
        "project_id",
        "action_ids",
        "field_mapping",
        "evidence_refs",
        "rationale",
        "expected_revision_digests",
        "conflicts",
        "applied",
        "owner_namespace",
    ]
    assert payload["conflicts"] == conflicts and payload["applied"] is False
    assert payload["field_mapping"] is mapping and payload["owner_namespace"] == "REVISION"
    assert digest == domain_digest("ACTION_MERGE_PROPOSAL", "1.0.0", canonical_payload(payload))
    first, second = ns(impact_set=["i1"]), ns(impact_set=["i2"])
    result = projection.merge_proposal_result(payload, digest, (first, None, second), request)
    assert result["merge_proposal"] == {**payload, "digest": digest}
    assert result["impact_preview"] == [["i1"], ["i2"]]
    assert result["lineage_preview"] == ["SUPERSEDES:action:1", "SUPERSEDES:action:2"]
