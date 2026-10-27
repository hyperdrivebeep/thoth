"""Earlier roles' statements reach later roles as labelled interpretations, not bare facts."""

from typing import cast

import pytest
from pydantic import ValidationError
from tests.unit.test_contextual_retrieval import context

from thoth.application.services.model_interpretation_view import with_model_interpretation_labels
from thoth.domain.canonical import canonical_payload
from thoth.domain.model import ContextPack
from thoth.domain.model_interpretation import MODEL_INTERPRETATION_CONTRACT

FINDING = {
    "statement": "10000열 fill_nan은 1.44.1에서 1.557s로 보고됐다.",
    "evidence_refs": ["span:a", "span:b"],
    "requirement_id": "check:0",
    "kind": "DOCUMENT_FACT",
}
NEGATIVE = {
    "statement": "원문에서 프로파일 결과를 찾아봤지만 없다.",
    "evidence_refs": ["span:a"],
    "requirement_id": None,
    "kind": "VALID_NEGATIVE_FINDING",
}


def with_research(**research: object) -> ContextPack:
    return context().model_copy(update={"research_context": research})


def view_items(labelled: ContextPack) -> list[dict[str, object]]:
    view = cast(dict[str, object], labelled.research_context["model_interpretations"])
    return cast(list[dict[str, object]], view["items"])


def test_findings_and_open_items_become_one_labelled_view_without_changing_the_text() -> None:
    original = with_research(
        confirmed_findings=[FINDING, NEGATIVE],
        interpretation_open_items=["측정 조건이 같은지 확인되지 않았다."],
        answer="답",
        task="Review",
    )
    before = canonical_payload(original)
    labelled = with_model_interpretation_labels(original)
    assert canonical_payload(original) == before
    research = labelled.research_context
    assert "confirmed_findings" not in research and "interpretation_open_items" not in research
    assert research["answer"] == "답" and research["task"] == "Review"
    view = cast(dict[str, object], research["model_interpretations"])
    assert view["schema_version"] == "1.0.0"
    assert view["contract"] == MODEL_INTERPRETATION_CONTRACT
    first, second, planner = view_items(labelled)
    assert first == {
        "label": "MODEL_INTERPRETATION",
        "producer_role": "SEMANTIC_REVIEWER",
        "item_kind": "REVIEWER_FINDING",
        "check": "CITED_SPAN_IDS_ONLY",
        "statement": FINDING["statement"],
        "evidence_refs": ["span:a", "span:b"],
        "requirement_id": "check:0",
        "finding_kind": "DOCUMENT_FACT",
    }
    assert second["finding_kind"] == "VALID_NEGATIVE_FINDING"
    assert "requirement_id" not in second
    assert planner == {
        "label": "MODEL_INTERPRETATION",
        "producer_role": "RESEARCH_PLANNER",
        "item_kind": "PLANNER_OPEN_ITEM",
        "check": "NOT_CHECKED",
        "statement": "측정 조건이 같은지 확인되지 않았다.",
        "evidence_refs": [],
    }


def test_labelling_twice_changes_nothing_more() -> None:
    once = with_model_interpretation_labels(
        with_research(confirmed_findings=[FINDING], interpretation_open_items=["열린 해석"])
    )
    assert with_model_interpretation_labels(once) is once


@pytest.mark.parametrize(
    "research",
    [
        {},
        {"confirmed_findings": [], "interpretation_open_items": []},
        {"confirmed_findings": [], "answer": "답"},
    ],
)
def test_nothing_to_label_leaves_the_context_as_it_was(research: dict[str, object]) -> None:
    original = with_research(**research)
    assert with_model_interpretation_labels(original) is original


@pytest.mark.parametrize(
    ("research", "expected"),
    [
        ({"confirmed_findings": [FINDING]}, ["REVIEWER_FINDING"]),
        ({"interpretation_open_items": ["열린 해석"]}, ["PLANNER_OPEN_ITEM"]),
        (
            {"confirmed_findings": [], "interpretation_open_items": ["열린 해석"]},
            ["PLANNER_OPEN_ITEM"],
        ),
    ],
)
def test_each_source_is_labelled_alone(research: dict[str, object], expected: list[str]) -> None:
    labelled = with_model_interpretation_labels(with_research(**research))
    assert [item["item_kind"] for item in view_items(labelled)] == expected
    assert (
        not {"confirmed_findings", "interpretation_open_items"} & labelled.research_context.keys()
    )


def test_a_malformed_stored_finding_is_refused_not_passed_on_bare() -> None:
    with pytest.raises(ValidationError):
        with_model_interpretation_labels(
            with_research(confirmed_findings=[{"statement": "근거와 종류가 없다"}])
        )
    with pytest.raises(ValueError, match="MODEL_INTERPRETATION_FINDINGS_INVALID"):
        with_model_interpretation_labels(with_research(confirmed_findings="not a list"))
    with pytest.raises(ValueError, match="MODEL_INTERPRETATION_OPEN_ITEMS_INVALID"):
        with_model_interpretation_labels(with_research(interpretation_open_items="not a list"))
