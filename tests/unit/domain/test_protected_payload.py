"""What is actually sent decides approval; how it is shown never does."""

from __future__ import annotations

from thoth.domain.protected_action import (
    material_changes,
    payload_digest,
    payload_from_step,
    word_diff,
)

CONTENT: dict[str, object] = {
    "body_text": "안녕하세요 담당자님 시험 성적서를 보내 주세요",
    "attachments": [{"name": "요청서.pdf", "digest": "1" * 64}],
}
STEP: dict[str, object] = {
    "step_id": "step:send",
    "action_ref": "action:1",
    "action_family": "COMMUNICATION_SUBMISSION",
    "target_digests": ["b" * 64],
    "scope": {"audience": "담당자"},
    "content": CONTENT,
    "channel": "EMAIL",
    "disclosed_data": ["프로젝트 이름"],
    "required_roles": ["project-owner"],
    "risk_tier": "R3",
    "reversibility": "IRREVERSIBLE",
    "effect_vector": {"external_write": True},
    "external_commitments": [],
    "execution_conditions": {"cutoff_at": "2026-09-30T00:00:00Z"},
    "presentation": {"label": "요청 메일", "order": 1, "collapsed": False},
    "state": "READY",
    "policy_state": "APPROVAL_REQUIRED",
    "required_processes": ["AUTHORIZATION"],
    "impact": {"x": 1},
}


def digest_of(**patch: object) -> str:
    return payload_digest(payload_from_step({**STEP, **patch}))


def test_presentation_and_derived_step_fields_do_not_change_the_approved_digest() -> None:
    base = digest_of()
    assert digest_of(presentation={"label": "다른 이름", "order": 9, "collapsed": True}) == base
    assert digest_of(order=9, color="red", collapsed=True) == base
    assert digest_of(state="RUNNING", policy_state="AUTO", impact={"y": 2}) == base


def test_a_title_or_summary_may_be_what_is_sent_so_changing_it_needs_a_new_approval() -> None:
    base = digest_of(title="요청 메일", summary="성적서 요청")
    assert digest_of(title="다른 제목", summary="성적서 요청") != base
    assert digest_of(title="요청 메일", summary="다른 요약") != base
    assert digest_of(label="표시 이름") != digest_of()
    assert digest_of(display_name="이름") != digest_of()
    before = payload_from_step({**STEP, "title": "요청 메일"})
    after = payload_from_step({**STEP, "title": "다른 제목"})
    (change,) = material_changes(before, after)
    assert change.label == "알 수 없는 새 항목"


def test_one_character_of_the_body_changes_the_digest() -> None:
    body = "안녕하세요 담당자님 시험 성적서를 보내 주세요"
    changed = digest_of(content={**CONTENT, "body_text": body + "."})
    assert changed != digest_of()


def test_each_of_the_ten_sent_items_changes_the_digest() -> None:
    base = digest_of()
    patches: list[dict[str, object]] = [
        {"action_family": "GOVERNANCE_ESCALATION"},
        {"target_digests": ["c" * 64]},
        {"scope": {"audience": "다른 팀"}},
        {"channel": "FORM"},
        {"disclosed_data": ["프로젝트 이름", "시험 결과"]},
        {"required_roles": ["project-owner", "test-owner"]},
        {"risk_tier": "R4"},
        {"reversibility": "REVERSIBLE"},
        {"external_commitments": ["3일 안에 회신"]},
        {"execution_conditions": {"cutoff_at": "2026-10-01T00:00:00Z"}},
    ]
    assert len({digest_of(**patch) for patch in patches} | {base}) == len(patches) + 1


def test_a_swapped_attachment_reports_its_name_and_digest_before_and_after() -> None:
    before = payload_from_step(STEP)
    after = payload_from_step(
        {
            **STEP,
            "content": {
                **CONTENT,
                "attachments": [{"name": "요청서-v2.pdf", "digest": "2" * 64}],
            },
        }
    )
    (change,) = material_changes(before, after)
    assert change.label == "첨부"
    (item,) = change.attachments
    assert (item.before_name, item.before_digest) == ("요청서.pdf", "1" * 64)
    assert (item.after_name, item.after_digest) == ("요청서-v2.pdf", "2" * 64)


def test_a_body_edit_is_reported_as_a_word_diff_of_only_the_changed_words() -> None:
    before = payload_from_step(STEP)
    after = payload_from_step(
        {
            **STEP,
            "content": {
                **CONTENT,
                "body_text": "안녕하세요 담당자님 시험 성적서와 원자료를 보내 주세요",
            },
        }
    )
    (change,) = material_changes(before, after)
    assert change.label == "본문"
    changed = [seg for seg in change.words if seg.op != "keep"]
    assert [(seg.op, seg.text) for seg in changed] == [
        ("del", "성적서를"),
        ("add", "성적서와 원자료를"),
    ]
    assert any(seg.op == "keep" and seg.text == "담당자님" for seg in change.words)


def test_an_unknown_new_field_is_material_and_named_as_unknown() -> None:
    before = payload_from_step(STEP)
    after = payload_from_step({**STEP, "cc_recipients": ["outside@example.com"]})
    assert payload_digest(before) != payload_digest(after)
    (change,) = material_changes(before, after)
    assert change.label == "알 수 없는 새 항목" and "cc_recipients" in change.after


def test_identical_payloads_have_no_changes_and_word_diff_of_equal_text_is_all_keep() -> None:
    payload = payload_from_step(STEP)
    assert material_changes(payload, payload_from_step(dict(STEP))) == ()
    assert {seg.op for seg in word_diff("가 나 다", "가 나 다")} == {"keep"}
