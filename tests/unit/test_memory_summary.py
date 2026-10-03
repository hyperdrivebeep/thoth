"""A stored memory reads as one sentence, never as the id of the record it points at."""

from __future__ import annotations

import pytest

from thoth.application.services.memory_safety import REDACTED_MEMORY, memory_text_is_unsafe
from thoth.application.services.memory_summary import UNREADABLE, summarize_record


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (
            {"statement": "INPUT_MATERIAL_DATA may explain the result", "hypothesis_id": "h:1"},
            "INPUT_MATERIAL_DATA may explain the result",
        ),
        ({"portfolio_id": "p:1", "hypothesis_refs": ["a", "b", "c"]}, "가설 3개 묶음"),
        ({"portfolio_id": "p:1", "hypotheses": [{}, {}]}, "가설 2개 묶음"),
        ({"plan_id": "plan:1", "selected_action_refs": ["x", "y"]}, "행동 2개 계획"),
        (
            {
                "action_id": "a:1",
                "specification": "문서에서 RFP 항목을 분리한다. 그 밖의 설명은 길다.",
            },
            "문서에서 RFP 항목을 분리한다.",
        ),
        (
            {"action_id": "a:1", "specification": {"description": "compare the dataset version"}},
            "compare the dataset version",
        ),
        (
            {
                "action_id": "a:1",
                "specification": {"title": "자료 버전 비교", "description": "긴 설명"},
            },
            "자료 버전 비교",
        ),
        (
            {"interpretation": "Reanalysis was recorded. Causality is not established."},
            "Reanalysis was recorded.",
        ),
        ({"statement": "가" * 400}, "가" * 199 + "…"),
    ],
)
def test_each_kind_of_record_gives_its_sentence(content: dict[str, object], expected: str) -> None:
    assert summarize_record(content) == expected


def test_an_unknown_shape_has_no_summary_so_the_caller_says_it_cannot_be_read() -> None:
    assert summarize_record({"object_id": "o:1"}) is None
    assert UNREADABLE == "내용을 읽을 수 없는 기억"


def test_the_redaction_marker_counts_as_unsafe_so_a_redacted_text_is_still_quarantined() -> None:
    assert memory_text_is_unsafe(REDACTED_MEMORY)
    assert memory_text_is_unsafe("api_key=supersecretvalue123")
    assert not memory_text_is_unsafe("표본이 작으면 결론을 보류한다")
