"""Roles that read the numbers are told to cite them, and only when the numbers are present."""

import pytest
from tests.unit.test_contextual_retrieval import context, work
from tests.unit.test_numeric_series import numeric_text, observed
from tests.unit.test_text_context_observation import bound, parse_text, spans

from thoth.application.services import numeric_observation_view
from thoth.application.services.numeric_observation_view import with_numeric_observations
from thoth.application.services.research_role_context import role_context
from thoth.domain.behavior_policy import RetrievalBehaviorPolicy
from thoth.domain.derived_calculation import NUMERIC_CITATION_INSTRUCTION
from thoth.domain.enums import ModelRole
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.research_execution import ResearchWork

CITING = (ModelRole.SEMANTIC_REVIEWER, ModelRole.HYPOTHESIS_GENERATOR)


def prepared() -> tuple[tuple[EvidenceSpan, ...], ResearchWork]:
    source = spans(bound(parse_text(numeric_text())))
    w = work()
    w.result_only["checked_observations"] = observed(numeric_text()).model_dump(mode="json")
    return source, w


@pytest.mark.parametrize("role", CITING)
def test_citing_roles_receive_the_instruction_next_to_the_calculations(role: ModelRole) -> None:
    source, w = prepared()
    received = with_numeric_observations(context(source), role, w)
    assert received.research_context["numeric_instruction"] == NUMERIC_CITATION_INSTRUCTION
    assert "checked_observations" in received.research_context


def test_the_adjudicator_sees_the_calculations_but_is_not_given_the_instruction() -> None:
    source, w = prepared()
    received = with_numeric_observations(context(source), ModelRole.REVIEW_ADJUDICATOR, w)
    assert "checked_observations" in received.research_context
    assert "numeric_instruction" not in received.research_context


@pytest.mark.parametrize("role", CITING)
def test_no_calculations_means_no_instruction_even_if_one_was_left_behind(
    role: ModelRole,
) -> None:
    source = spans(bound(parse_text(numeric_text())))
    stale = context(source).model_copy(
        update={"research_context": {"numeric_instruction": NUMERIC_CITATION_INSTRUCTION}}
    )
    received = with_numeric_observations(stale, role, work())
    assert received.research_context == {}


def test_a_view_that_could_not_be_delivered_carries_no_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, w = prepared()
    monkeypatch.setattr(
        numeric_observation_view,
        "retrieval_policy",
        lambda: RetrievalBehaviorPolicy(character_budget=1),
    )
    received = with_numeric_observations(context(source), ModelRole.SEMANTIC_REVIEWER, w)
    assert "checked_observations" not in received.research_context
    assert "numeric_instruction" not in received.research_context


def test_the_reviewer_role_view_keeps_the_instruction_and_other_roles_never_get_it() -> None:
    source, w = prepared()
    received = with_numeric_observations(context(source), ModelRole.SEMANTIC_REVIEWER, w)
    kept = role_context(received, ModelRole.SEMANTIC_REVIEWER).research_context
    assert kept["numeric_instruction"] == NUMERIC_CITATION_INSTRUCTION
    for role in (ModelRole.RESEARCH_PLANNER, ModelRole.EVIDENCE_RERANKER):
        assert "numeric_instruction" not in role_context(received, role).research_context
