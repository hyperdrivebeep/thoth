"""Bundles are recognised in one place; new memories carry a short body, not the whole record."""

from datetime import UTC, datetime

from thoth.application.services.domain_reference_memory import domain_reference_memory
from thoth.application.services.memory_shape import (
    RecordShape,
    is_container,
    key_fields,
    member_keys,
    memory_body,
    record_shape,
    summarize_record,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import EntityType
from thoth.domain.revision import SemanticRevision

HYPOTHESIS = {
    "hypothesis_id": "hypothesis:o:one",
    "statement": "표본이 작으면 결론을 보류한다. 두 번째 문장은 본문에 넣지 않는다.",
    "empirical_appraisal": "UNASSESSED",
    "portfolio_id": "portfolio:o:1",
    "evidence_refs": ["e1"] * 30,
}
ACTION = {
    "action_id": "action:o:compare",
    "portfolio_id": "portfolio:o:1",
    "specification": {"description": "출처별로 대조한다"},
    "primary_purpose": "ANALYSIS_COMPUTATION",
}
OUTCOME = {"outcome_id": "outcome:o:1", "interpretation": "측정 한계를 결론에 적는다"}
HYPOTHESIS_PORTFOLIO = {
    "portfolio_id": "portfolio:o:1",
    "hypothesis_refs": ["hypothesis:o:one", "hypothesis:o:two", "hypothesis:o:three"],
}
ACTION_PLAN = {"plan_id": "plan:o:1", "selected_action_refs": ["action:o:compare"], "steps": []}
ACTION_PORTFOLIO = {"portfolio_id": "action-portfolio:o", "action_refs": ["action:o:compare"] * 3}


def _revision(entity_type: EntityType, entity_id: str) -> SemanticRevision:
    draft: dict[str, object] = {
        "revision_id": f"revision:{entity_id}",
        "project_id": "project-1",
        "entity_type": entity_type.value,
        "entity_id": entity_id,
        "snapshot_id": f"snapshot:{entity_id}",
        "parent_revision_digests": [],
        "actor": {"actor_id": "agent:test", "kind": "AGENT", "role": "test", "project_id": "p"},
        "reason": "test",
        "evidence_refs": [],
        "affected_refs": [],
        "created_at": datetime(2026, 9, 1, tzinfo=UTC),
    }
    return SemanticRevision.model_validate(
        {**draft, "revision_digest": domain_digest("T", "1", canonical_payload(draft))}
    )


def test_the_three_kinds_of_bundle_are_told_apart_from_single_records() -> None:
    assert record_shape(HYPOTHESIS) == RecordShape.HYPOTHESIS
    assert record_shape(ACTION) == RecordShape.ACTION
    assert record_shape(OUTCOME) == RecordShape.OUTCOME
    for bundle in (HYPOTHESIS_PORTFOLIO, ACTION_PLAN, ACTION_PORTFOLIO):
        assert is_container(bundle)
    for single in (HYPOTHESIS, ACTION, OUTCOME):
        assert not is_container(single)


def test_the_readable_line_of_every_bundle_names_how_many_it_groups() -> None:
    assert summarize_record(HYPOTHESIS_PORTFOLIO) == "가설 3개 묶음"
    assert summarize_record(ACTION_PLAN) == "행동 1개 계획"
    assert summarize_record(ACTION_PORTFOLIO) == "행동 3개 묶음"


def test_a_bundle_lists_the_ledger_keys_of_its_members() -> None:
    assert member_keys(HYPOTHESIS_PORTFOLIO) == {
        "HYPOTHESIS:hypothesis:o:one",
        "HYPOTHESIS:hypothesis:o:two",
        "HYPOTHESIS:hypothesis:o:three",
    }
    assert member_keys(ACTION_PLAN) == {"ACTION:action:o:compare"}
    assert member_keys(HYPOTHESIS) == frozenset()


def test_a_new_body_is_one_line_with_the_fields_that_matter() -> None:
    text, words = memory_body(HYPOTHESIS) or ("", "")
    assert text.startswith("표본이 작으면 결론을 보류한다.")
    assert "empirical_appraisal: UNASSESSED" in text
    assert "두 번째 문장" not in text and len(text) < 200
    assert "empirical_appraisal" not in words and "표본이" in words
    action_text, _ = memory_body(ACTION) or ("", "")
    assert action_text == "출처별로 대조한다 | purpose: ANALYSIS_COMPUTATION"
    assert (memory_body(OUTCOME) or ("", ""))[0] == "측정 한계를 결론에 적는다"
    assert memory_body(ACTION_PLAN) is None


def test_key_fields_are_the_ones_whose_difference_is_a_real_change() -> None:
    assert key_fields(HYPOTHESIS) == {
        "statement": HYPOTHESIS["statement"],
        "empirical_appraisal": "UNASSESSED",
    }
    assert key_fields(ACTION_PLAN) == {}


def test_no_memory_is_made_for_a_bundle_and_one_is_made_for_each_member() -> None:
    revision = _revision(EntityType.HYPOTHESIS, "hypothesis:o:one")
    assert domain_reference_memory(revision, "m-1", HYPOTHESIS) is not None
    for entity_type, entity_id, bundle in (
        (EntityType.HYPOTHESIS, "portfolio:o:1", HYPOTHESIS_PORTFOLIO),
        (EntityType.ACTION, "plan:o:1", ACTION_PLAN),
        (EntityType.ACTION, "action-portfolio:o", ACTION_PORTFOLIO),
    ):
        assert domain_reference_memory(_revision(entity_type, entity_id), "m-2", bundle) is None
    action = domain_reference_memory(
        _revision(EntityType.ACTION, "action:o:compare"), "m-3", ACTION
    )
    assert action is not None and action.source_ref == "ACTION:action:o:compare"
